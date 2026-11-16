package org.firox.stream;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Firox Stream's wire protocol, as /usr/bin/firox-stream speaks it.
 *
 * One TLS connection (TCP 48300, the host's self-signed certificate). Every
 * message is a kind letter, a 4-byte big-endian length and the payload (JSON
 * or media). Handshake: H (hello: name, token) -> N/W (pair: a PIN shown on
 * the host) <- P (proof), B (someone's streaming) <- J (join/takeover),
 * T (a token to keep), O (the stream starts: host info).
 */
public final class Protocol {
    public static final int PORT = 48300;
    public static final String SERVICE = "_firox-stream._tcp.";
    static final int MAX_MESSAGE = 32 << 20;

    public static final class Message {
        public final char kind;
        public final byte[] payload;

        Message(char kind, byte[] payload) {
            this.kind = kind;
            this.payload = payload;
        }

        public JSONObject json() {
            try {
                return new JSONObject(new String(payload, StandardCharsets.UTF_8));
            } catch (JSONException e) {
                return new JSONObject();
            }
        }
    }

    /** Asked on the connecting thread; implementations block until the user answers. */
    public interface Questions {
        /** The PIN shown on the host, or null to give up. */
        String pin(String hostName, String note);

        /** "join", "takeover" or null, when others are streaming it already. */
        String busy(List<String> names);
    }

    public static final class Session {
        public final SSLSocket socket;
        public final DataInputStream in;
        public final OutputStream out;
        public final JSONObject info;
        public final String fingerprint;

        Session(SSLSocket socket, DataInputStream in, OutputStream out, JSONObject info, String fingerprint) {
            this.socket = socket;
            this.in = in;
            this.out = out;
            this.info = info;
            this.fingerprint = fingerprint;
        }

        public void close() {
            try {
                socket.close();
            } catch (IOException ignored) {
            }
        }
    }

    private Protocol() {
    }

    public static byte[] frame(char kind, byte[] payload) {
        byte[] out = new byte[5 + payload.length];
        out[0] = (byte) kind;
        int n = payload.length;
        out[1] = (byte) (n >>> 24);
        out[2] = (byte) (n >>> 16);
        out[3] = (byte) (n >>> 8);
        out[4] = (byte) n;
        System.arraycopy(payload, 0, out, 5, n);
        return out;
    }

    public static byte[] frame(char kind, Object json) {
        return frame(kind, json == null ? new byte[0] : json.toString().getBytes(StandardCharsets.UTF_8));
    }

    public static Message read(DataInputStream in, int limit) throws IOException {
        int kind = in.read();
        if (kind < 0) {
            throw new EOFException("connection closed");
        }
        int n = in.readInt();
        if (n < 0 || n > limit) {
            throw new IOException("message too long");
        }
        byte[] payload = new byte[n];
        in.readFully(payload);
        return new Message((char) kind, payload);
    }

    /** What this device calls itself to the host ("Pixel 8", or the name set in Settings). */
    public static String deviceName(Context ctx) {
        String name = null;
        try {
            name = Settings.Global.getString(ctx.getContentResolver(), Settings.Global.DEVICE_NAME);
        } catch (Exception ignored) {
        }
        if (name == null || name.trim().isEmpty()) {
            name = Build.MODEL;
        }
        return name.trim();
    }

    /** What proves the PIN was typed: it covers the certificate as this device saw it. */
    public static String proof(String pin, String fingerprint, String name) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(pin.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        byte[] sig = mac.doFinal(("firox-stream|" + fingerprint + "|" + name).getBytes(StandardCharsets.UTF_8));
        return hex(sig);
    }

    static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }

    // ---------------------------------------------------------------- hosts we've paired with

    static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences("hosts", Context.MODE_PRIVATE);
    }

    /** fingerprint -> {name, address, port, token, last} */
    public static JSONObject hosts(Context ctx) {
        try {
            return new JSONObject(prefs(ctx).getString("hosts", "{}"));
        } catch (JSONException e) {
            return new JSONObject();
        }
    }

    static void saveHosts(Context ctx, JSONObject hosts) {
        prefs(ctx).edit().putString("hosts", hosts.toString()).apply();
    }

    public static void forget(Context ctx, String fingerprint) {
        JSONObject hosts = hosts(ctx);
        hosts.remove(fingerprint);
        saveHosts(ctx, hosts);
    }

    // ---------------------------------------------------------------- connecting

    /** Where paired hosts are kept: fingerprint -> {name, address, port, token}. */
    public interface Store {
        JSONObject get(String fingerprint);

        void put(String fingerprint, JSONObject host);

        /**
         * The certificate fingerprint a computer at this address must have
         * (one paired before there), or null: then any certificate is
         * accepted, and pairing with the PIN shown on that computer proves
         * which one it is.
         */
        String pinned(String host, int port);
    }

    /**
     * The computer answered with a different certificate from the one it had
     * when this device paired with it. Nothing is sent to it.
     */
    public static final class IdentityChangedException extends IOException {
        public final String expected, actual;

        IdentityChangedException(String expected, String actual) {
            super("Its identity has changed since this device paired with it.");
            this.expected = expected;
            this.actual = actual;
        }
    }

    /**
     * The host's certificate is self-signed, so it can't be checked against
     * certificate authorities. Instead it's pinned: once paired, a computer
     * must always present the same certificate (by SHA-256 fingerprint),
     * checked here during the TLS handshake, before anything is sent.
     */
    static final class PinningTrustManager implements X509TrustManager {
        private final String expected;
        volatile String fingerprint;
        volatile boolean mismatch;

        PinningTrustManager(String expected) {
            this.expected = expected;
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String auth) throws CertificateException {
            throw new CertificateException("this app never accepts connections");
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String auth) throws CertificateException {
            if (chain == null || chain.length == 0) {
                throw new CertificateException("no certificate");
            }
            try {
                fingerprint = hex(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()));
            } catch (Exception e) {
                throw new CertificateException(e);
            }
            if (expected != null && !expected.equalsIgnoreCase(fingerprint)) {
                mismatch = true;
                throw new CertificateException("certificate changed");
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }

    /** Connect and pair if needed. Blocking: call from a background thread. */
    public static Session connect(Context ctx, String host, int port, String fingerprint, Questions ask)
            throws Exception {
        Context app = ctx.getApplicationContext();
        return connect(host, port, deviceName(app), ask, new Store() {
            public JSONObject get(String fp) {
                return hosts(app).optJSONObject(fp);
            }

            public String pinned(String h, int p) {
                if (fingerprint != null) {
                    return fingerprint;         // picked from the list of paired computers
                }
                JSONObject all = hosts(app);
                for (Iterator<String> it = all.keys(); it.hasNext(); ) {
                    String fp = it.next();
                    JSONObject e = all.optJSONObject(fp);
                    if (e != null && h.equalsIgnoreCase(e.optString("address"))
                            && p == e.optInt("port", PORT)) {
                        return fp;
                    }
                }
                return null;
            }

            public void put(String fp, JSONObject h) {
                try {
                    JSONObject all = hosts(app);
                    all.put(fp, h);
                    saveHosts(app, all);
                } catch (JSONException ignored) {
                }
            }
        });
    }

    /** The handshake itself (plain Java, so it can be tried against a host off the phone). */
    public static Session connect(String host, int port, String name, Questions ask, Store store) throws Exception {
        Socket raw = new Socket();
        raw.connect(new InetSocketAddress(host, port), 10000);
        raw.setTcpNoDelay(true);
        raw.setKeepAlive(true);
        String expected = store.pinned(host, port);
        PinningTrustManager trust = new PinningTrustManager(expected);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, new TrustManager[]{trust}, new SecureRandom());
        SSLSocket sock = (SSLSocket) tls.getSocketFactory().createSocket(raw, host, port, true);
        sock.setEnabledProtocols(tlsVersions(sock.getSupportedProtocols()));
        try {
            try {
                sock.startHandshake();
            } catch (SSLHandshakeException e) {
                if (trust.mismatch) {
                    throw new IdentityChangedException(expected, trust.fingerprint);
                }
                throw e;
            }
            String fingerprint = trust.fingerprint;
            if (fingerprint == null) {
                throw new IOException("The other computer didn't identify itself.");
            }
            DataInputStream in = new DataInputStream(new BufferedInputStream(sock.getInputStream(), 1 << 16));
            OutputStream out = new BufferedOutputStream(sock.getOutputStream(), 1 << 16);

            JSONObject known = store.get(fingerprint);
            known = known == null ? new JSONObject() : new JSONObject(known.toString());
            JSONObject hello = new JSONObject();
            hello.put("v", 1);
            hello.put("name", name);
            hello.put("token", known.optString("token", ""));
            send(out, frame('H', hello));
            // Starting the capture can take a while on the host
            sock.setSoTimeout(90000);
            String note = "";
            while (true) {
                Message m = read(in, 65536);
                JSONObject msg = m.json();
                switch (m.kind) {
                    case 'E':
                        throw new IOException(msg.optString("error", "The other computer refused."));
                    case 'N':
                    case 'W': {
                        if (m.kind == 'W') {
                            int left = msg.optInt("left");
                            note = "Wrong PIN. " + left + (left == 1 ? " try" : " tries") + " left.";
                        }
                        String hostName = msg.optString("name", known.optString("name", host));
                        sock.setSoTimeout(0);
                        String pin = ask.pin(hostName, note);
                        sock.setSoTimeout(90000);
                        if (pin == null || pin.trim().isEmpty()) {
                            throw new CancelledException();
                        }
                        known.put("name", hostName);
                        JSONObject p = new JSONObject();
                        p.put("proof", proof(pin.trim(), fingerprint, name));
                        send(out, frame('P', p));
                        break;
                    }
                    case 'B': {
                        List<String> names = new ArrayList<>();
                        JSONArray arr = msg.optJSONArray("viewers");
                        for (int i = 0; arr != null && i < arr.length(); i++) {
                            names.add(arr.optString(i));
                        }
                        sock.setSoTimeout(0);
                        String mode = ask.busy(names);
                        sock.setSoTimeout(90000);
                        if (mode == null) {
                            throw new CancelledException();
                        }
                        JSONObject j = new JSONObject();
                        j.put("mode", mode);
                        send(out, frame('J', j));
                        break;
                    }
                    case 'T':
                        known.put("token", msg.optString("token", ""));
                        known.put("paired", System.currentTimeMillis() / 1000.0);
                        break;
                    case 'O': {
                        known.put("address", host);
                        known.put("port", port);
                        if (msg.has("name")) {
                            known.put("name", msg.optString("name"));
                        }
                        known.put("last", System.currentTimeMillis() / 1000.0);
                        if (known.has("token")) {
                            store.put(fingerprint, known);
                        }
                        sock.setSoTimeout(0);
                        return new Session(sock, in, out, msg, fingerprint);
                    }
                    default:
                        throw new IOException("The other computer answered something unexpected.");
                }
            }
        } catch (Exception e) {
            try {
                sock.close();
            } catch (IOException ignored) {
            }
            throw e;
        }
    }

    /** TLS 1.2 and 1.3 only (the host's Python offers both). */
    static String[] tlsVersions(String[] supported) {
        List<String> out = new ArrayList<>();
        for (String v : supported) {
            if (v.equals("TLSv1.3") || v.equals("TLSv1.2")) {
                out.add(v);
            }
        }
        return out.isEmpty() ? supported : out.toArray(new String[0]);
    }

    static void send(OutputStream out, byte[] data) throws IOException {
        out.write(data);
        out.flush();
    }

    /** The user said no (cancelled the PIN or the join question). */
    public static final class CancelledException extends Exception {
        CancelledException() {
            super("");
        }
    }

    static void closeQuietly(InputStream in) {
        try {
            in.close();
        } catch (IOException ignored) {
        }
    }
}
