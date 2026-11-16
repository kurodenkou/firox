package org.firox.stream;

import android.content.Context;
import android.hardware.input.InputManager;
import android.os.Build;
import android.os.CombinedVibration;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.util.SparseArray;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Game controllers on this device, sent to the host the way the desktop
 * client sends them ("G": SDL's button bits, sticks -32768..32767 with y
 * down, triggers 0..32767), where each becomes a virtual Xbox 360
 * controller. Rumble comes back ("R") and is played on the controller.
 */
final class Gamepads implements InputManager.InputDeviceListener {
    // SDL's button numbers (the wire format)
    static final int A = 0, B = 1, X = 2, Y = 3, BACK = 4, GUIDE = 5, START = 6, LSTICK = 7, RSTICK = 8,
            LB = 9, RB = 10, UP = 11, DOWN = 12, LEFT = 13, RIGHT = 14;

    interface Sender {
        void send(JSONObject msg);
    }

    interface Notes {
        void note(String text);
    }

    static final class Pad {
        final int deviceId, slot;
        final String name;
        int keys;                       // buttons from key events
        int hat;                        // D-pad from the hat axes
        int[] axes = new int[6];
        int[] last;

        Pad(int deviceId, int slot, String name) {
            this.deviceId = deviceId;
            this.slot = slot;
            this.name = name;
        }

        int[] state() {
            return new int[]{keys | hat, axes[0], axes[1], axes[2], axes[3], axes[4], axes[5]};
        }
    }

    private final SparseArray<Pad> pads = new SparseArray<>();
    private final Sender sender;
    private final Notes notes;
    private final InputManager input;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean active = true;

    Gamepads(Context ctx, Sender sender, Notes notes) {
        this.sender = sender;
        this.notes = notes;
        input = (InputManager) ctx.getSystemService(Context.INPUT_SERVICE);
        input.registerInputDeviceListener(this, ui);
    }

    void close() {
        input.unregisterInputDeviceListener(this);
    }

    static boolean isGamepad(InputDevice d) {
        if (d == null || d.isVirtual()) {
            return false;
        }
        int s = d.getSources();
        return (s & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
                || (s & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
    }

    private Pad pad(InputDevice d) {
        if (!isGamepad(d)) {
            return null;
        }
        Pad p = pads.get(d.getId());
        if (p == null) {
            boolean[] used = new boolean[4];
            for (int i = 0; i < pads.size(); i++) {
                used[pads.valueAt(i).slot] = true;
            }
            int slot = -1;
            for (int i = 0; i < 4; i++) {
                if (!used[i]) {
                    slot = i;
                    break;
                }
            }
            if (slot < 0) {
                return null;            // four already
            }
            p = new Pad(d.getId(), slot, d.getName());
            pads.put(d.getId(), p);
            notes.note("Controller " + (slot + 1) + " connected: " + d.getName());
        }
        return p;
    }

    void setActive(boolean on) {
        if (on == active) {
            return;
        }
        active = on;
        if (!on) {
            try {
                sender.send(new JSONObject().put("idle", 1));
            } catch (Exception ignored) {
            }
            for (int i = 0; i < pads.size(); i++) {
                pads.valueAt(i).last = null;       // everything again when back
            }
        }
    }

    private static int bit(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_BUTTON_A: return A;
            case KeyEvent.KEYCODE_BUTTON_B: return B;
            case KeyEvent.KEYCODE_BUTTON_X: return X;
            case KeyEvent.KEYCODE_BUTTON_Y: return Y;
            case KeyEvent.KEYCODE_BUTTON_SELECT:
            case KeyEvent.KEYCODE_BACK: return BACK;
            case KeyEvent.KEYCODE_BUTTON_MODE: return GUIDE;
            case KeyEvent.KEYCODE_BUTTON_START:
            case KeyEvent.KEYCODE_MENU: return START;
            case KeyEvent.KEYCODE_BUTTON_THUMBL: return LSTICK;
            case KeyEvent.KEYCODE_BUTTON_THUMBR: return RSTICK;
            case KeyEvent.KEYCODE_BUTTON_L1: return LB;
            case KeyEvent.KEYCODE_BUTTON_R1: return RB;
            case KeyEvent.KEYCODE_DPAD_UP: return UP;
            case KeyEvent.KEYCODE_DPAD_DOWN: return DOWN;
            case KeyEvent.KEYCODE_DPAD_LEFT: return LEFT;
            case KeyEvent.KEYCODE_DPAD_RIGHT: return RIGHT;
            default: return -1;
        }
    }

    /** A key event from a controller: true if it was one of its buttons. */
    boolean onKey(KeyEvent ev) {
        Pad p = pad(ev.getDevice());
        if (p == null) {
            return false;
        }
        int b = bit(ev.getKeyCode());
        if (b < 0) {
            // L2/R2 as buttons only (no trigger axes): full or nothing
            if (ev.getKeyCode() == KeyEvent.KEYCODE_BUTTON_L2 || ev.getKeyCode() == KeyEvent.KEYCODE_BUTTON_R2) {
                InputDevice d = ev.getDevice();
                boolean hasAxis = d.getMotionRange(MotionEvent.AXIS_LTRIGGER) != null
                        || d.getMotionRange(MotionEvent.AXIS_BRAKE) != null;
                if (!hasAxis) {
                    int i = ev.getKeyCode() == KeyEvent.KEYCODE_BUTTON_L2 ? 4 : 5;
                    p.axes[i] = ev.getAction() == KeyEvent.ACTION_DOWN ? 32767 : 0;
                    push(p);
                }
                return true;
            }
            return false;
        }
        if (ev.getAction() == KeyEvent.ACTION_DOWN) {
            p.keys |= 1 << b;
        } else if (ev.getAction() == KeyEvent.ACTION_UP) {
            p.keys &= ~(1 << b);
        }
        push(p);
        return true;
    }

    /** Stick, trigger and hat movement from a controller. */
    boolean onMotion(MotionEvent ev) {
        if ((ev.getSource() & InputDevice.SOURCE_JOYSTICK) != InputDevice.SOURCE_JOYSTICK
                || ev.getAction() != MotionEvent.ACTION_MOVE) {
            return false;
        }
        Pad p = pad(ev.getDevice());
        if (p == null) {
            return false;
        }
        InputDevice d = ev.getDevice();
        p.axes[0] = stick(ev, d, MotionEvent.AXIS_X);
        p.axes[1] = stick(ev, d, MotionEvent.AXIS_Y);
        p.axes[2] = stick(ev, d, MotionEvent.AXIS_Z);
        p.axes[3] = stick(ev, d, MotionEvent.AXIS_RZ);
        p.axes[4] = trigger(ev, d, MotionEvent.AXIS_LTRIGGER, MotionEvent.AXIS_BRAKE);
        p.axes[5] = trigger(ev, d, MotionEvent.AXIS_RTRIGGER, MotionEvent.AXIS_GAS);
        float hx = ev.getAxisValue(MotionEvent.AXIS_HAT_X), hy = ev.getAxisValue(MotionEvent.AXIS_HAT_Y);
        int hat = 0;
        if (hx < -0.5f) hat |= 1 << LEFT;
        if (hx > 0.5f) hat |= 1 << RIGHT;
        if (hy < -0.5f) hat |= 1 << UP;
        if (hy > 0.5f) hat |= 1 << DOWN;
        p.hat = hat;
        push(p);
        return true;
    }

    private static int stick(MotionEvent ev, InputDevice d, int axis) {
        InputDevice.MotionRange r = d.getMotionRange(axis, ev.getSource());
        if (r == null) {
            return 0;
        }
        float v = ev.getAxisValue(axis);
        if (Math.abs(v) <= r.getFlat()) {
            v = 0;
        }
        return Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
    }

    private static int trigger(MotionEvent ev, InputDevice d, int axis, int alt) {
        float v = 0;
        if (d.getMotionRange(axis, ev.getSource()) != null) {
            v = ev.getAxisValue(axis);
        }
        if (d.getMotionRange(alt, ev.getSource()) != null) {
            v = Math.max(v, ev.getAxisValue(alt));
        }
        return Math.max(0, Math.min(32767, Math.round(v * 32767)));
    }

    private void push(Pad p) {
        if (!active) {
            return;
        }
        int[] s = p.state();
        if (p.last != null && java.util.Arrays.equals(s, p.last)) {
            return;
        }
        boolean first = p.last == null;
        p.last = s;
        try {
            JSONArray arr = new JSONArray();
            for (int v : s) {
                arr.put(v);
            }
            JSONObject msg = new JSONObject().put("p", p.slot).put("s", arr);
            if (first) {
                msg.put("name", p.name);
            }
            sender.send(msg);
        } catch (Exception ignored) {
        }
    }

    /** Rumble from the host (strong/weak motor 0..65535, for ms; 0 stops). */
    void rumble(int slot, int low, int high, int ms) {
        for (int i = 0; i < pads.size(); i++) {
            Pad p = pads.valueAt(i);
            if (p.slot != slot) {
                continue;
            }
            InputDevice d = InputDevice.getDevice(p.deviceId);
            if (d == null) {
                return;
            }
            int amp = Math.max(low, high) * 255 / 65535;
            try {
                if (Build.VERSION.SDK_INT >= 31) {
                    VibratorManager vm = d.getVibratorManager();
                    int[] ids = vm.getVibratorIds();
                    if (ids.length == 0) {
                        return;
                    }
                    if (amp == 0 || ms == 0) {
                        vm.cancel();
                    } else if (ids.length >= 2) {
                        // Two motors: the strong (low-frequency) one and the weak one
                        CombinedVibration.ParallelCombination c = CombinedVibration.startParallel();
                        c.addVibrator(ids[0], VibrationEffect.createOneShot(ms, clamp(low * 255 / 65535)));
                        c.addVibrator(ids[1], VibrationEffect.createOneShot(ms, clamp(high * 255 / 65535)));
                        vm.vibrate(c.combine());
                    } else {
                        vm.vibrate(CombinedVibration.createParallel(VibrationEffect.createOneShot(ms, clamp(amp))));
                    }
                } else {
                    Vibrator v = d.getVibrator();
                    if (!v.hasVibrator()) {
                        return;
                    }
                    if (amp == 0 || ms == 0) {
                        v.cancel();
                    } else {
                        v.vibrate(VibrationEffect.createOneShot(ms, clamp(amp)));
                    }
                }
            } catch (Exception ignored) {
            }
            return;
        }
    }

    private static int clamp(int amp) {
        return Math.max(1, Math.min(255, amp));
    }

    @Override
    public void onInputDeviceAdded(int deviceId) {
        // Made on its first button or stick movement
    }

    @Override
    public void onInputDeviceRemoved(int deviceId) {
        Pad p = pads.get(deviceId);
        if (p == null) {
            return;
        }
        pads.remove(deviceId);
        try {
            sender.send(new JSONObject().put("p", p.slot).put("gone", 1));
        } catch (Exception ignored) {
        }
        notes.note("Controller " + (p.slot + 1) + " unplugged");
    }

    @Override
    public void onInputDeviceChanged(int deviceId) {
    }
}
