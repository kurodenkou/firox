package org.firox.stream;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.Context;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;

/** Pick a Firox computer to stream: the ones on this network, the ones paired before, or an address. */
public class MainActivity extends Activity {

    /** The connection handed to StreamActivity (a socket can't travel in an Intent). */
    static Protocol.Session session;

    static final class Entry {
        String name, address, fingerprint, detail;
        int port;
        boolean found;

        @Override
        public String toString() {
            return name;
        }
    }

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<String, Entry> found = new LinkedHashMap<>();   // address:port -> entry
    private final List<Entry> shown = new ArrayList<>();
    private ArrayAdapter<Entry> adapter;
    private TextView status;
    private NsdManager nsd;
    private NsdManager.DiscoveryListener discovery;
    private WifiManager.MulticastLock multicast;
    private final List<NsdServiceInfo> toResolve = new ArrayList<>();
    private boolean resolving;
    private boolean connecting;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        int pad = dp(16);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("Firox Stream");
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 26);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        TextView intro = new TextView(this);
        intro.setText("Play a Firox computer's games and desktop here. On that computer, open Firox Stream "
                + "and tick “Let other computers stream this one”.");
        intro.setPadding(0, dp(6), 0, dp(10));
        root.addView(intro);

        status = new TextView(this);
        status.setText("Looking for computers on this network…");
        status.setAlpha(0.7f);
        root.addView(status);

        ListView list = new ListView(this);
        adapter = new ArrayAdapter<Entry>(this, android.R.layout.simple_list_item_2, android.R.id.text1, shown) {
            @Override
            public View getView(int position, View convertView, ViewGroup parent) {
                View v = super.getView(position, convertView, parent);
                Entry e = getItem(position);
                ((TextView) v.findViewById(android.R.id.text1)).setText(e.name);
                ((TextView) v.findViewById(android.R.id.text2)).setText(e.detail);
                return v;
            }
        };
        list.setAdapter(adapter);
        list.setOnItemClickListener((p, v, pos, id) -> connect(shown.get(pos)));
        list.setOnItemLongClickListener((p, v, pos, id) -> {
            Entry e = shown.get(pos);
            if (e.fingerprint == null) {
                return false;
            }
            new AlertDialog.Builder(this)
                    .setTitle("Forget " + e.name + "?")
                    .setMessage("Connecting again will need a new PIN from that computer.")
                    .setPositiveButton("Forget", (d, w) -> {
                        Protocol.forget(this, e.fingerprint);
                        refresh();
                    })
                    .setNegativeButton("Cancel", null)
                    .show();
            return true;
        });
        root.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        Button manual = new Button(this);
        manual.setText("Connect to an address…");
        manual.setOnClickListener(v -> askAddress());
        root.addView(manual);

        TextView foot = new TextView(this);
        foot.setText("Long-press a paired computer to forget it.");
        foot.setAlpha(0.6f);
        foot.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        foot.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(foot);
        setContentView(root);
        nsd = (NsdManager) getSystemService(Context.NSD_SERVICE);
    }

    @Override
    protected void onResume() {
        super.onResume();
        connecting = false;
        refresh();
        startDiscovery();
    }

    @Override
    protected void onPause() {
        super.onPause();
        stopDiscovery();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    // ---------------------------------------------------------------- the list

    private void refresh() {
        shown.clear();
        Map<String, Entry> byAddress = new LinkedHashMap<>(found);
        JSONObject hosts = Protocol.hosts(this);
        for (Iterator<String> it = hosts.keys(); it.hasNext(); ) {
            String fp = it.next();
            JSONObject h = hosts.optJSONObject(fp);
            if (h == null) {
                continue;
            }
            String key = h.optString("address") + ":" + h.optInt("port", Protocol.PORT);
            Entry e = byAddress.remove(key);
            if (e == null) {
                e = new Entry();
                e.address = h.optString("address");
                e.port = h.optInt("port", Protocol.PORT);
                e.name = h.optString("name", e.address);
                e.detail = "Paired · " + e.address;
            } else {
                e.detail = "On this network · paired";
            }
            e.fingerprint = fp;
            shown.add(e);
        }
        shown.addAll(0, byAddress.values());
        adapter.notifyDataSetChanged();
        status.setText(found.isEmpty() ? "Looking for computers on this network…"
                : "Tap a computer to stream it.");
    }

    private void startDiscovery() {
        if (discovery != null) {
            return;
        }
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            multicast = wifi.createMulticastLock("firox-stream");
            multicast.setReferenceCounted(false);
            multicast.acquire();
        } catch (Exception ignored) {
        }
        discovery = new NsdManager.DiscoveryListener() {
            public void onStartDiscoveryFailed(String type, int code) {
                ui.post(() -> status.setText("Couldn't look for computers (" + code + "). Use an address instead."));
            }

            public void onStopDiscoveryFailed(String type, int code) {
            }

            public void onDiscoveryStarted(String type) {
            }

            public void onDiscoveryStopped(String type) {
            }

            public void onServiceFound(NsdServiceInfo info) {
                ui.post(() -> {
                    toResolve.add(info);
                    resolveNext();
                });
            }

            public void onServiceLost(NsdServiceInfo info) {
                ui.post(() -> {
                    for (Iterator<Map.Entry<String, Entry>> it = found.entrySet().iterator(); it.hasNext(); ) {
                        if (it.next().getValue().name.equals(info.getServiceName())) {
                            it.remove();
                        }
                    }
                    refresh();
                });
            }
        };
        try {
            nsd.discoverServices(Protocol.SERVICE, NsdManager.PROTOCOL_DNS_SD, discovery);
        } catch (Exception e) {
            discovery = null;
        }
    }

    // Older Android resolves one service at a time
    private void resolveNext() {
        if (resolving || toResolve.isEmpty()) {
            return;
        }
        resolving = true;
        NsdServiceInfo info = toResolve.remove(0);
        nsd.resolveService(info, new NsdManager.ResolveListener() {
            public void onResolveFailed(NsdServiceInfo i, int code) {
                ui.post(() -> {
                    resolving = false;
                    resolveNext();
                });
            }

            public void onServiceResolved(NsdServiceInfo i) {
                ui.post(() -> {
                    resolving = false;
                    if (i.getHost() != null && i.getHost().getAddress().length == 4) {
                        Entry e = new Entry();
                        e.name = i.getServiceName();
                        e.address = i.getHost().getHostAddress();
                        e.port = i.getPort() > 0 ? i.getPort() : Protocol.PORT;
                        e.found = true;
                        e.detail = "On this network · " + e.address;
                        found.put(e.address + ":" + e.port, e);
                        refresh();
                    }
                    resolveNext();
                });
            }
        });
    }

    private void stopDiscovery() {
        if (discovery != null) {
            try {
                nsd.stopServiceDiscovery(discovery);
            } catch (Exception ignored) {
            }
            discovery = null;
        }
        if (multicast != null && multicast.isHeld()) {
            multicast.release();
        }
        toResolve.clear();
        resolving = false;
    }

    private void askAddress() {
        EditText field = new EditText(this);
        field.setHint("192.168.1.20 or name:port");
        field.setSingleLine(true);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        field.setImeOptions(EditorInfo.IME_ACTION_GO);
        field.setText(Protocol.prefs(this).getString("last_address", ""));
        AlertDialog d = new AlertDialog.Builder(this)
                .setTitle("Connect to an address")
                .setView(pad(field))
                .setPositiveButton("Connect", (dlg, w) -> connectAddress(field.getText().toString()))
                .setNegativeButton("Cancel", null)
                .create();
        field.setOnEditorActionListener((v, action, ev) -> {
            d.dismiss();
            connectAddress(field.getText().toString());
            return true;
        });
        d.show();
    }

    private void connectAddress(String text) {
        text = text.trim();
        if (text.isEmpty()) {
            return;
        }
        Protocol.prefs(this).edit().putString("last_address", text).apply();
        Entry e = new Entry();
        e.port = Protocol.PORT;
        e.address = text;
        if (text.startsWith("[") && text.contains("]:")) {
            e.address = text.substring(1, text.indexOf("]:"));
            e.port = parsePort(text.substring(text.indexOf("]:") + 2));
        } else if (text.indexOf(':') == text.lastIndexOf(':') && text.contains(":")) {
            e.address = text.substring(0, text.indexOf(':'));
            e.port = parsePort(text.substring(text.indexOf(':') + 1));
        }
        e.address = e.address.replace("[", "").replace("]", "");
        e.name = text;
        connect(e);
    }

    private static int parsePort(String s) {
        try {
            return Integer.parseInt(s.trim());
        } catch (NumberFormatException e) {
            return Protocol.PORT;
        }
    }

    private View pad(View v) {
        LinearLayout box = new LinearLayout(this);
        box.setPadding(dp(20), dp(8), dp(20), 0);
        box.addView(v, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return box;
    }

    // ---------------------------------------------------------------- connecting

    private void connect(Entry e) {
        if (connecting) {
            return;
        }
        connecting = true;
        ProgressDialog progress = new ProgressDialog(this);
        progress.setMessage("Connecting to " + e.name + "…");
        progress.setCancelable(false);
        progress.show();
        Context app = getApplicationContext();
        new Thread(() -> {
            try {
                Protocol.Session s = Protocol.connect(app, e.address, e.port, e.fingerprint, new Protocol.Questions() {
                    public String pin(String hostName, String note) {
                        return askPin(progress, hostName, note);
                    }

                    public String busy(List<String> names) {
                        return askBusy(progress, names);
                    }
                });
                ui.post(() -> {
                    progress.dismiss();
                    session = s;
                    startActivity(new Intent(this, StreamActivity.class));
                });
            } catch (Protocol.IdentityChangedException changed) {
                ui.post(() -> {
                    progress.dismiss();
                    connecting = false;
                    if (!isFinishing()) {
                        identityChanged(e, changed);
                    }
                });
            } catch (Protocol.CancelledException c) {
                ui.post(() -> {
                    progress.dismiss();
                    connecting = false;
                });
            } catch (Exception ex) {
                String why = explain(ex);
                ui.post(() -> {
                    progress.dismiss();
                    connecting = false;
                    if (!isFinishing()) {
                        new AlertDialog.Builder(this)
                                .setTitle("Couldn't connect to " + e.name)
                                .setMessage(why)
                                .setPositiveButton("OK", null)
                                .show();
                    }
                });
            }
        }, "connect").start();
    }

    /** A paired computer showed a different certificate: say so, and let the user decide. */
    private void identityChanged(Entry e, Protocol.IdentityChangedException changed) {
        new AlertDialog.Builder(this)
                .setTitle(e.name + " isn't the computer you paired with")
                .setMessage("Its identity has changed since this device paired with it, so nothing was sent "
                        + "to it.\n\nIf it was reinstalled, or its Firox Stream settings were reset, forget it "
                        + "here and pair again with the PIN it shows. Otherwise something on this network may "
                        + "be pretending to be it: don't pair.\n\nPaired: " + shortPrint(changed.expected)
                        + "\nNow: " + shortPrint(changed.actual))
                .setPositiveButton("Close", null)
                .setNegativeButton("Forget It", (d, w) -> {
                    if (changed.expected != null) {
                        Protocol.forget(this, changed.expected);
                    }
                    refresh();
                })
                .show();
    }

    private static String shortPrint(String fp) {
        if (fp == null || fp.length() < 16) {
            return String.valueOf(fp);
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 16; i += 4) {
            sb.append(i > 0 ? " " : "").append(fp, i, i + 4);
        }
        return sb.append("\u2026").toString();
    }

    static String explain(Exception e) {
        String m = e.getMessage();
        if (e instanceof java.net.ConnectException) {
            return "Nothing answered. Is Firox Stream turned on there (“Let other computers stream this "
                    + "one”), and is it on the same network?";
        }
        if (e instanceof java.net.SocketTimeoutException) {
            return "It didn't answer in time.";
        }
        if (e instanceof java.net.UnknownHostException) {
            return "No computer has that name.";
        }
        if (e instanceof java.net.NoRouteToHostException) {
            return "There's no way to reach it from here (a different network, or its firewall).";
        }
        return m == null || m.isEmpty() ? e.getClass().getSimpleName() : m;
    }

    /** Ask on the UI thread and wait for the answer (the connecting thread calls this). */
    private String askPin(ProgressDialog progress, String hostName, String note) {
        BlockingQueue<String> answer = new ArrayBlockingQueue<>(1);
        ui.post(() -> {
            progress.hide();
            EditText field = new EditText(this);
            field.setInputType(InputType.TYPE_CLASS_NUMBER);
            field.setHint("PIN");
            field.setTextSize(TypedValue.COMPLEX_UNIT_SP, 28);
            field.setGravity(Gravity.CENTER);
            LinearLayout box = new LinearLayout(this);
            box.setOrientation(LinearLayout.VERTICAL);
            box.setPadding(dp(20), dp(8), dp(20), 0);
            TextView msg = new TextView(this);
            msg.setText((note.isEmpty() ? "" : note + "\n\n") + "Type the 4-digit PIN that " + hostName
                    + " shows now.");
            box.addView(msg);
            box.addView(field);
            new AlertDialog.Builder(this)
                    .setTitle("Pair with " + hostName)
                    .setView(box)
                    .setCancelable(false)
                    .setPositiveButton("Pair", (d, w) -> {
                        progress.show();
                        answer.offer(field.getText().toString());
                    })
                    .setNegativeButton("Cancel", (d, w) -> answer.offer(""))
                    .show();
        });
        try {
            return answer.take();
        } catch (InterruptedException e) {
            return null;
        }
    }

    private String askBusy(ProgressDialog progress, List<String> names) {
        BlockingQueue<String> answer = new ArrayBlockingQueue<>(1);
        ui.post(() -> {
            progress.hide();
            new AlertDialog.Builder(this)
                    .setTitle("Someone is streaming it already")
                    .setMessage(String.join(", ", names) + (names.size() == 1 ? " is" : " are")
                            + " streaming that computer. Join them, or take over (which disconnects them)?")
                    .setCancelable(false)
                    .setPositiveButton("Join", (d, w) -> {
                        progress.show();
                        answer.offer("join");
                    })
                    .setNeutralButton("Take Over", (d, w) -> {
                        progress.show();
                        answer.offer("takeover");
                    })
                    .setNegativeButton("Cancel", (d, w) -> answer.offer("-"))
                    .show();
        });
        try {
            String a = answer.take();
            return "-".equals(a) ? null : a;
        } catch (InterruptedException e) {
            return null;
        }
    }
}
