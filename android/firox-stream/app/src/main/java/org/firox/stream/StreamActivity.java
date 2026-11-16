package org.firox.stream;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.inputmethod.BaseInputConnection;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The stream: the host's picture and sound, with this device's touch screen,
 * mouse, keyboard and game controllers sent back.
 *
 * Touch works like a mouse on the picture: tap to click, long-press to
 * right-click, drag to drag, two fingers to scroll. A real mouse moves the
 * pointer directly, or, captured (menu), moves it by relative motion for games.
 */
public class StreamActivity extends Activity implements SurfaceHolder.Callback {
    static final int BTN_LEFT = 0x110, BTN_RIGHT = 0x111, BTN_MIDDLE = 0x112;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService writer = Executors.newSingleThreadExecutor();
    private Protocol.Session session;
    private JSONObject info;
    private volatile boolean ended;
    private Thread reader;

    private FrameLayout root;
    private StreamView view;
    private TextView hint;
    private Media.Video video;
    private final Media.Audio audio = new Media.Audio();
    private volatile boolean surfaceReady;
    private int videoW = 1280, videoH = 720;
    private long lastKeyAsk;
    private boolean locked;

    private Gamepads pads;
    private final Set<Integer> downKeys = new HashSet<>();
    private final Set<Integer> downButtons = new HashSet<>();
    private boolean captured;

    // touch state
    private float downX, downY, lastX, lastY, scrollAcc;
    private long downTime;
    private boolean dragging, longPressed, twoFinger;
    private final Runnable longPress = this::onLongPress;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        session = MainActivity.session;
        MainActivity.session = null;
        if (session == null) {
            finish();
            return;
        }
        info = session.info;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 28) {
            getWindow().getAttributes().layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        view = new StreamView(this);
        view.getHolder().addCallback(this);
        root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER));

        TextView menu = new TextView(this);
        menu.setText("⋮");
        menu.setTextColor(Color.WHITE);
        menu.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        menu.setGravity(Gravity.CENTER);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0x66000000);
        bg.setCornerRadius(dp(20));
        menu.setBackground(bg);
        menu.setOnClickListener(this::showMenu);
        FrameLayout.LayoutParams mlp = new FrameLayout.LayoutParams(dp(40), dp(40), Gravity.TOP | Gravity.END);
        mlp.setMargins(0, dp(10), dp(10), 0);
        root.addView(menu, mlp);

        hint = new TextView(this);
        hint.setTextColor(Color.WHITE);
        hint.setPadding(dp(14), dp(8), dp(14), dp(8));
        GradientDrawable hbg = new GradientDrawable();
        hbg.setColor(0xAA000000);
        hbg.setCornerRadius(dp(8));
        hint.setBackground(hbg);
        FrameLayout.LayoutParams hlp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        hlp.setMargins(dp(60), dp(12), dp(60), 0);
        root.addView(hint, hlp);
        setContentView(root);
        immersive();

        String name = info.optString("name", "the other computer");
        if (!info.optBoolean("input")) {
            showHint("View only: " + name + " can't take this device's input", 6);
        } else {
            showHint("Tap to click · long-press to right-click · two fingers to scroll · ⋮ for more", 6);
        }
        if (info.optBoolean("gamepads")) {
            pads = new Gamepads(this, msg -> send('G', msg), text -> ui.post(() -> showHint(text, 3)));
        }
        JSONArray viewers = info.optJSONArray("viewers");
        if (viewers != null && viewers.length() > 0 && !info.optBoolean("locked")) {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < viewers.length(); i++) {
                sb.append(i > 0 ? ", " : "").append(viewers.optString(i));
            }
            ui.postDelayed(() -> showHint("Also watching: " + sb, 5), 6500);
        }
        if (info.optBoolean("locked")) {
            ui.postDelayed(() -> setLocked(true), 1500);
        }
        reader = new Thread(this::readLoop, "stream");
        reader.start();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void immersive() {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            immersive();
        } else {
            releaseAll();
        }
        if (pads != null) {
            pads.setActive(hasFocus);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        releaseAll();
        if (captured && Build.VERSION.SDK_INT >= 26) {
            view.releasePointerCapture();
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (pads != null) {
            pads.close();
        }
        stop(false);
    }

    // ---------------------------------------------------------------- network

    private void send(char kind, Object json) {
        if (ended || session == null) {
            return;
        }
        byte[] data = Protocol.frame(kind, json);
        writer.execute(() -> {
            try {
                Protocol.send(session.out, data);
            } catch (IOException e) {
                // the reader notices and ends the stream
            }
        });
    }

    private void sendInput(Object... ev) {
        if (!info.optBoolean("input")) {
            return;
        }
        JSONArray one = new JSONArray();
        for (Object o : ev) {
            one.put(o);
        }
        send('I', new JSONArray().put(one));
    }

    private static final Pattern WIDTH = Pattern.compile("width=\\(int\\)(\\d+)");
    private static final Pattern HEIGHT = Pattern.compile("height=\\(int\\)(\\d+)");
    private static final Pattern RATE = Pattern.compile("rate=\\(int\\)(\\d+)");
    private static final Pattern CHANNELS = Pattern.compile("channels=\\(int\\)(\\d+)");

    private static int find(Pattern p, String s) {
        Matcher m = p.matcher(s);
        return m.find() ? Integer.parseInt(m.group(1)) : 0;
    }

    private void readLoop() {
        String reason = "";
        try {
            while (!ended) {
                Protocol.Message m = Protocol.read(session.in, Protocol.MAX_MESSAGE);
                switch (m.kind) {
                    case 'V':
                        onVideo(m.payload);
                        break;
                    case 'A':
                        if (info.optBoolean("audio")) {
                            audio.feed(m.payload);
                        }
                        break;
                    case 'K': {
                        JSONObject msg = m.json();
                        String caps = msg.optString("caps");
                        if ("video".equals(msg.optString("kind"))) {
                            int w = find(WIDTH, caps), h = find(HEIGHT, caps);
                            if (w > 0 && h > 0) {
                                videoW = w;
                                videoH = h;
                                synchronized (this) {
                                    if (video != null) {
                                        video.setSize(w, h);
                                    }
                                }
                                ui.post(this::layoutVideo);
                            }
                        } else if ("audio".equals(msg.optString("kind"))) {
                            audio.setFormat(find(RATE, caps), find(CHANNELS, caps));
                        }
                        break;
                    }
                    case 'S': {
                        JSONObject msg = m.json();
                        String text = msg.has("joined") ? msg.optString("joined") + " joined"
                                : msg.has("left") ? msg.optString("left") + " left" : "";
                        if (!text.isEmpty() && !locked) {
                            ui.post(() -> showHint(text, 4));
                        }
                        break;
                    }
                    case 'L': {
                        boolean l = m.json().optBoolean("locked");
                        ui.post(() -> setLocked(l));
                        break;
                    }
                    case 'u': {
                        JSONObject msg = m.json();
                        ui.post(() -> unlockAnswer(msg.optBoolean("ok"), msg.optString("message")));
                        break;
                    }
                    case 'R': {
                        JSONObject msg = m.json();
                        if (pads != null) {
                            int slot = msg.optInt("p"), lo = msg.optInt("lo") & 0xffff,
                                    hi = msg.optInt("hi") & 0xffff, ms = Math.max(0, msg.optInt("ms"));
                            ui.post(() -> pads.rumble(slot, lo, hi, ms));
                        }
                        break;
                    }
                    case 'E':
                        reason = m.json().optString("error", "");
                        ended = true;
                        break;
                    default:
                        break;
                }
            }
        } catch (IOException e) {
            reason = ended ? "" : "The connection was lost.";
        } catch (Exception e) {
            reason = "Something went wrong: " + e;
        }
        String why = reason;
        ui.post(() -> streamEnded(why));
    }

    private void onVideo(byte[] au) {
        synchronized (this) {
            if (!surfaceReady) {
                return;
            }
            if (video == null) {
                video = new Media.Video(view.getHolder().getSurface());
                video.setSize(videoW, videoH);
            }
            if (!video.feed(au)) {
                long now = System.currentTimeMillis();
                if (now - lastKeyAsk > 1000) {        // waiting for a key frame: ask for one
                    lastKeyAsk = now;
                    send('F', null);
                }
            }
        }
    }

    private void streamEnded(String reason) {
        if (isFinishing()) {
            return;
        }
        stop(false);
        if (reason == null || reason.isEmpty() || "stopped".equals(reason)) {
            finish();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("The stream ended")
                .setMessage(reason)
                .setCancelable(false)
                .setPositiveButton("OK", (d, w) -> finish())
                .show();
    }

    private void stop(boolean sayGoodbye) {
        if (session == null) {
            return;
        }
        Protocol.Session s = session;
        if (sayGoodbye && !ended) {
            releaseAll();
            send('Q', null);
        }
        ended = true;
        writer.execute(s::close);
        writer.shutdown();
        synchronized (this) {
            if (video != null) {
                video.release();
                video = null;
            }
        }
        audio.release();
        session = null;
    }

    private void disconnect() {
        stop(true);
        finish();
    }

    // ---------------------------------------------------------------- picture

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        synchronized (this) {
            surfaceReady = true;
        }
        send('F', null);                 // a new decoder needs a key frame
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        synchronized (this) {
            surfaceReady = false;
            if (video != null) {
                video.release();
                video = null;
            }
        }
    }

    /** Letterbox the picture to its own shape. */
    private void layoutVideo() {
        int w = root.getWidth(), h = root.getHeight();
        if (w == 0 || h == 0) {
            root.post(this::layoutVideo);
            return;
        }
        float scale = Math.min(w / (float) videoW, h / (float) videoH);
        FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) view.getLayoutParams();
        lp.width = Math.round(videoW * scale);
        lp.height = Math.round(videoH * scale);
        lp.gravity = Gravity.CENTER;
        view.setLayoutParams(lp);
    }

    // ---------------------------------------------------------------- overlay

    private final Runnable hideHint = () -> hint.animate().alpha(0f).setDuration(400);

    private void showHint(String text, int seconds) {
        hint.setText(text);
        hint.animate().cancel();
        hint.setAlpha(1f);
        ui.removeCallbacks(hideHint);
        if (seconds > 0) {
            ui.postDelayed(hideHint, seconds * 1000L);
        }
    }

    private void showMenu(View anchor) {
        PopupMenu m = new PopupMenu(this, anchor);
        m.getMenu().add(0, 1, 0, "Keyboard");
        if (Build.VERSION.SDK_INT >= 26) {
            m.getMenu().add(0, 2, 0, captured ? "Release the mouse" : "Capture the mouse (games)");
        }
        m.getMenu().add(0, 3, 0, "Wake its screen");
        m.getMenu().add(0, 4, 0, "Unlock…");
        m.getMenu().add(0, 5, 0, "Disconnect");
        m.setOnMenuItemClickListener(item -> {
            switch (item.getItemId()) {
                case 1:
                    view.requestFocus();
                    ((InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE))
                            .showSoftInput(view, InputMethodManager.SHOW_FORCED);
                    return true;
                case 2:
                    toggleCapture();
                    return true;
                case 3:
                    send('W', null);
                    showHint("Waking its screen", 2);
                    return true;
                case 4:
                    askUnlock();
                    return true;
                case 5:
                    disconnect();
                    return true;
            }
            return false;
        });
        m.show();
    }

    private void setLocked(boolean l) {
        locked = l;
        if (l) {
            showHint("That computer is locked · ⋮ → Unlock… to type its password", 0);
        } else {
            showHint("Unlocked", 2);
        }
    }

    private void askUnlock() {
        EditText field = new EditText(this);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        field.setHint("Password");
        LinearLayout box = new LinearLayout(this);
        box.setPadding(dp(20), dp(8), dp(20), 0);
        box.addView(field, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        new AlertDialog.Builder(this)
                .setTitle("Unlock " + info.optString("name", "it"))
                .setMessage("The password of the account locked there. Its lock screen checks it as if "
                        + "typed at that computer.")
                .setView(box)
                .setPositiveButton("Unlock", (d, w) -> {
                    try {
                        send('U', new JSONObject().put("password", field.getText().toString()));
                    } catch (Exception ignored) {
                    }
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void unlockAnswer(boolean ok, String message) {
        if (ok) {
            setLocked(false);
        } else {
            new AlertDialog.Builder(this)
                    .setTitle("Not unlocked")
                    .setMessage(message.isEmpty() ? "That password didn't unlock it." : message)
                    .setPositiveButton("Try Again", (d, w) -> askUnlock())
                    .setNegativeButton("Cancel", null)
                    .show();
        }
    }

    @Override
    public void onBackPressed() {
        new AlertDialog.Builder(this)
                .setTitle("Stop streaming?")
                .setPositiveButton("Disconnect", (d, w) -> disconnect())
                .setNegativeButton("Keep Streaming", null)
                .show();
    }

    // ---------------------------------------------------------------- touch and mouse

    private float[] toVideo(float x, float y) {
        float u = x / Math.max(1, view.getWidth()), v = y / Math.max(1, view.getHeight());
        return new float[]{Math.max(0f, Math.min(0.9999f, u)), Math.max(0f, Math.min(0.9999f, v))};
    }

    private void moveTo(float x, float y) {
        float[] uv = toVideo(x, y);
        sendInput("m", round4(uv[0]), round4(uv[1]));
    }

    private static double round4(float f) {
        return Math.round(f * 10000) / 10000.0;
    }

    private void button(int code, boolean down) {
        if (down == downButtons.contains(code)) {
            return;
        }
        if (down) {
            downButtons.add(code);
        } else {
            downButtons.remove(code);
        }
        sendInput("b", code, down);
    }

    private void click(int code) {
        button(code, true);
        button(code, false);
    }

    boolean onTouch(MotionEvent ev) {
        if (ev.getToolType(0) == MotionEvent.TOOL_TYPE_MOUSE) {
            return onMouse(ev);
        }
        int slop = dp(12);
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = lastX = ev.getX();
                downY = lastY = ev.getY();
                downTime = ev.getEventTime();
                dragging = longPressed = twoFinger = false;
                scrollAcc = 0;
                moveTo(downX, downY);
                ui.postDelayed(longPress, 550);
                return true;
            case MotionEvent.ACTION_POINTER_DOWN:
                twoFinger = true;
                ui.removeCallbacks(longPress);
                if (dragging) {
                    button(BTN_LEFT, false);
                    dragging = false;
                }
                lastY = (ev.getY(0) + ev.getY(1)) / 2;
                return true;
            case MotionEvent.ACTION_MOVE:
                if (twoFinger && ev.getPointerCount() >= 2) {
                    float y = (ev.getY(0) + ev.getY(1)) / 2;
                    scrollAcc += (lastY - y) / dp(40);           // fingers up: scroll down
                    lastY = y;
                    if (Math.abs(scrollAcc) >= 0.25f) {
                        sendInput("s", 0, Math.round(scrollAcc * 100) / 100.0);
                        scrollAcc = 0;
                    }
                    return true;
                }
                if (twoFinger || longPressed) {
                    return true;
                }
                float x = ev.getX(), y = ev.getY();
                if (!dragging && Math.hypot(x - downX, y - downY) > slop) {
                    ui.removeCallbacks(longPress);
                    dragging = true;
                    moveTo(downX, downY);
                    button(BTN_LEFT, true);
                }
                if (dragging) {
                    moveTo(x, y);
                }
                return true;
            case MotionEvent.ACTION_UP:
                ui.removeCallbacks(longPress);
                if (dragging) {
                    moveTo(ev.getX(), ev.getY());
                    button(BTN_LEFT, false);
                } else if (!longPressed && !twoFinger) {
                    click(BTN_LEFT);
                }
                dragging = false;
                return true;
            case MotionEvent.ACTION_CANCEL:
                ui.removeCallbacks(longPress);
                if (dragging) {
                    button(BTN_LEFT, false);
                }
                dragging = false;
                return true;
        }
        return true;
    }

    private void onLongPress() {
        if (!dragging && !twoFinger) {
            longPressed = true;
            view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS);
            click(BTN_RIGHT);
        }
    }

    private static int mouseButton(int androidButton) {
        switch (androidButton) {
            case MotionEvent.BUTTON_PRIMARY: return BTN_LEFT;
            case MotionEvent.BUTTON_SECONDARY: return BTN_RIGHT;
            case MotionEvent.BUTTON_TERTIARY: return BTN_MIDDLE;
            case MotionEvent.BUTTON_BACK: return 0x113;
            case MotionEvent.BUTTON_FORWARD: return 0x114;
            default: return 0;
        }
    }

    /** A real mouse over the picture: where it is, its buttons and its wheel. */
    boolean onMouse(MotionEvent ev) {
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_HOVER_MOVE:
            case MotionEvent.ACTION_MOVE:
                moveTo(ev.getX(), ev.getY());
                return true;
            case MotionEvent.ACTION_BUTTON_PRESS:
            case MotionEvent.ACTION_BUTTON_RELEASE: {
                int code = mouseButton(ev.getActionButton());
                if (code != 0) {
                    moveTo(ev.getX(), ev.getY());
                    button(code, ev.getActionMasked() == MotionEvent.ACTION_BUTTON_PRESS);
                }
                return true;
            }
            case MotionEvent.ACTION_SCROLL: {
                float h = ev.getAxisValue(MotionEvent.AXIS_HSCROLL), v = ev.getAxisValue(MotionEvent.AXIS_VSCROLL);
                // Android: up is +1; the host's wheel takes down as positive
                sendInput("s", Math.round(h * 100) / 100.0, Math.round(-v * 100) / 100.0);
                return true;
            }
            case MotionEvent.ACTION_DOWN:
            case MotionEvent.ACTION_UP:
                return true;            // the BUTTON_PRESS/RELEASE events carry the buttons
        }
        return false;
    }

    private void toggleCapture() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        if (captured) {
            view.releasePointerCapture();
        } else {
            view.requestFocus();
            view.requestPointerCapture();
        }
    }

    void onCaptureChanged(boolean on) {
        captured = on;
        showHint(on ? "Mouse captured: it moves that computer's mouse. ⋮ to release it" : "Mouse released", 3);
        if (!on) {
            for (Integer code : new HashSet<>(downButtons)) {
                button(code, false);
            }
        }
    }

    /** The captured mouse: movement, not a position. */
    boolean onCaptured(MotionEvent ev) {
        int n = ev.getHistorySize();
        float dx = 0, dy = 0;
        for (int i = 0; i < n; i++) {
            dx += ev.getHistoricalAxisValue(MotionEvent.AXIS_RELATIVE_X, i);
            dy += ev.getHistoricalAxisValue(MotionEvent.AXIS_RELATIVE_Y, i);
        }
        dx += ev.getAxisValue(MotionEvent.AXIS_RELATIVE_X);
        dy += ev.getAxisValue(MotionEvent.AXIS_RELATIVE_Y);
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_BUTTON_PRESS:
            case MotionEvent.ACTION_BUTTON_RELEASE: {
                int code = mouseButton(ev.getActionButton());
                if (code != 0) {
                    button(code, ev.getActionMasked() == MotionEvent.ACTION_BUTTON_PRESS);
                }
                return true;
            }
            case MotionEvent.ACTION_SCROLL:
                sendInput("s", 0, Math.round(-ev.getAxisValue(MotionEvent.AXIS_VSCROLL) * 100) / 100.0);
                return true;
        }
        if (dx != 0 || dy != 0) {
            sendInput("d", Math.round(dx * 100) / 100.0, Math.round(dy * 100) / 100.0);
        }
        return true;
    }

    // ---------------------------------------------------------------- keyboard and controllers

    @Override
    public boolean dispatchKeyEvent(KeyEvent ev) {
        if (pads != null && Gamepads.isGamepad(ev.getDevice()) && pads.onKey(ev)) {
            return true;
        }
        int code = ev.getKeyCode();
        if (code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_VOLUME_UP
                || code == KeyEvent.KEYCODE_VOLUME_DOWN || code == KeyEvent.KEYCODE_HOME) {
            return super.dispatchKeyEvent(ev);   // this device's own keys
        }
        // Ctrl+Alt+Shift+Z releases a captured mouse, as on the desktop
        if (captured && code == KeyEvent.KEYCODE_Z && ev.isCtrlPressed() && ev.isAltPressed() && ev.isShiftPressed()) {
            if (ev.getAction() == KeyEvent.ACTION_DOWN) {
                toggleCapture();
            }
            return true;
        }
        int linux = Keys.linux(code);
        if (linux == 0) {
            if (ev.getAction() == KeyEvent.ACTION_MULTIPLE && ev.getCharacters() != null) {
                typeText(ev.getCharacters());
                return true;
            }
            return super.dispatchKeyEvent(ev);
        }
        if (ev.getAction() == KeyEvent.ACTION_DOWN) {
            if (ev.getRepeatCount() == 0 && downKeys.add(linux)) {
                sendInput("k", linux, true);
            }
        } else if (ev.getAction() == KeyEvent.ACTION_UP) {
            if (downKeys.remove(linux)) {
                sendInput("k", linux, false);
            }
        }
        return true;
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent ev) {
        if (pads != null && pads.onMotion(ev)) {
            return true;
        }
        return super.dispatchGenericMotionEvent(ev);
    }

    /** Text from an on-screen keyboard: typed as keys on a US layout. */
    void typeText(CharSequence text) {
        for (int i = 0; i < text.length(); i++) {
            int[] k = Keys.forChar(text.charAt(i));
            if (k == null) {
                continue;
            }
            if (k[1] == 1) {
                sendInput("k", Keys.KEY_LEFTSHIFT, true);
            }
            sendInput("k", k[0], true);
            sendInput("k", k[0], false);
            if (k[1] == 1) {
                sendInput("k", Keys.KEY_LEFTSHIFT, false);
            }
        }
    }

    void pressKey(int linux) {
        sendInput("k", linux, true);
        sendInput("k", linux, false);
    }

    private void releaseAll() {
        for (Integer code : downKeys) {
            sendInput("k", code, false);
        }
        for (Integer code : downButtons) {
            sendInput("b", code, false);
        }
        downKeys.clear();
        downButtons.clear();
    }

    /** The picture: takes touch, mouse and on-screen keyboard input. */
    final class StreamView extends SurfaceView {
        StreamView(Context ctx) {
            super(ctx);
            setFocusable(true);
            setFocusableInTouchMode(true);
            if (Build.VERSION.SDK_INT >= 26) {
                setOnCapturedPointerListener((v, ev) -> onCaptured(ev));
            }
        }

        @Override
        public boolean onTouchEvent(MotionEvent ev) {
            return onTouch(ev);
        }

        @Override
        public boolean onGenericMotionEvent(MotionEvent ev) {
            if (ev.isFromSource(InputDevice.SOURCE_MOUSE)) {
                return onMouse(ev);
            }
            return super.onGenericMotionEvent(ev);
        }

        @Override
        public void onPointerCaptureChange(boolean hasCapture) {
            super.onPointerCaptureChange(hasCapture);
            onCaptureChanged(hasCapture);
        }

        @Override
        public boolean onCheckIsTextEditor() {
            return true;
        }

        @Override
        public InputConnection onCreateInputConnection(EditorInfo out) {
            // No text field here: keys go straight to the other computer
            out.inputType = InputType.TYPE_NULL;
            out.imeOptions = EditorInfo.IME_FLAG_NO_FULLSCREEN | EditorInfo.IME_FLAG_NO_EXTRACT_UI;
            return new BaseInputConnection(this, false) {
                @Override
                public boolean commitText(CharSequence text, int newCursorPosition) {
                    typeText(text);
                    return true;
                }

                @Override
                public boolean deleteSurroundingText(int before, int after) {
                    for (int i = 0; i < Math.max(1, before); i++) {
                        pressKey(Keys.KEY_BACKSPACE);
                    }
                    return true;
                }

                @Override
                public boolean sendKeyEvent(KeyEvent event) {
                    return dispatchKeyEvent(event);
                }
            };
        }
    }
}
