package org.firox.stream;

import android.util.SparseIntArray;
import android.view.KeyEvent;

/**
 * Android key codes to Linux input key codes (what the host's virtual
 * keyboard types: evdev KEY_*), and typed characters to keys on a US layout
 * (for on-screen keyboards that send text rather than keys).
 */
final class Keys {
    static final int KEY_LEFTSHIFT = 42, KEY_BACKSPACE = 14, KEY_ENTER = 28;
    private static final SparseIntArray MAP = new SparseIntArray();

    private Keys() {
    }

    private static void m(int android, int linux) {
        MAP.put(android, linux);
    }

    static {
        // Letters
        int[] letters = {30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17,
                45, 21, 44};
        for (int i = 0; i < 26; i++) {
            m(KeyEvent.KEYCODE_A + i, letters[i]);
        }
        // Digits: 1..9 are 2..10, 0 is 11
        m(KeyEvent.KEYCODE_0, 11);
        for (int i = 1; i <= 9; i++) {
            m(KeyEvent.KEYCODE_0 + i, 1 + i);
        }
        m(KeyEvent.KEYCODE_ESCAPE, 1);
        m(KeyEvent.KEYCODE_MINUS, 12);
        m(KeyEvent.KEYCODE_EQUALS, 13);
        m(KeyEvent.KEYCODE_DEL, 14);
        m(KeyEvent.KEYCODE_TAB, 15);
        m(KeyEvent.KEYCODE_LEFT_BRACKET, 26);
        m(KeyEvent.KEYCODE_RIGHT_BRACKET, 27);
        m(KeyEvent.KEYCODE_ENTER, 28);
        m(KeyEvent.KEYCODE_CTRL_LEFT, 29);
        m(KeyEvent.KEYCODE_SEMICOLON, 39);
        m(KeyEvent.KEYCODE_APOSTROPHE, 40);
        m(KeyEvent.KEYCODE_GRAVE, 41);
        m(KeyEvent.KEYCODE_SHIFT_LEFT, 42);
        m(KeyEvent.KEYCODE_BACKSLASH, 43);
        m(KeyEvent.KEYCODE_COMMA, 51);
        m(KeyEvent.KEYCODE_PERIOD, 52);
        m(KeyEvent.KEYCODE_SLASH, 53);
        m(KeyEvent.KEYCODE_SHIFT_RIGHT, 54);
        m(KeyEvent.KEYCODE_NUMPAD_MULTIPLY, 55);
        m(KeyEvent.KEYCODE_ALT_LEFT, 56);
        m(KeyEvent.KEYCODE_SPACE, 57);
        m(KeyEvent.KEYCODE_CAPS_LOCK, 58);
        for (int i = 0; i < 10; i++) {
            m(KeyEvent.KEYCODE_F1 + i, 59 + i);                  // F1..F10
        }
        m(KeyEvent.KEYCODE_NUM_LOCK, 69);
        m(KeyEvent.KEYCODE_SCROLL_LOCK, 70);
        m(KeyEvent.KEYCODE_NUMPAD_7, 71);
        m(KeyEvent.KEYCODE_NUMPAD_8, 72);
        m(KeyEvent.KEYCODE_NUMPAD_9, 73);
        m(KeyEvent.KEYCODE_NUMPAD_SUBTRACT, 74);
        m(KeyEvent.KEYCODE_NUMPAD_4, 75);
        m(KeyEvent.KEYCODE_NUMPAD_5, 76);
        m(KeyEvent.KEYCODE_NUMPAD_6, 77);
        m(KeyEvent.KEYCODE_NUMPAD_ADD, 78);
        m(KeyEvent.KEYCODE_NUMPAD_1, 79);
        m(KeyEvent.KEYCODE_NUMPAD_2, 80);
        m(KeyEvent.KEYCODE_NUMPAD_3, 81);
        m(KeyEvent.KEYCODE_NUMPAD_0, 82);
        m(KeyEvent.KEYCODE_NUMPAD_DOT, 83);
        m(KeyEvent.KEYCODE_F11, 87);
        m(KeyEvent.KEYCODE_F12, 88);
        m(KeyEvent.KEYCODE_NUMPAD_ENTER, 96);
        m(KeyEvent.KEYCODE_CTRL_RIGHT, 97);
        m(KeyEvent.KEYCODE_NUMPAD_DIVIDE, 98);
        m(KeyEvent.KEYCODE_SYSRQ, 99);
        m(KeyEvent.KEYCODE_ALT_RIGHT, 100);
        m(KeyEvent.KEYCODE_MOVE_HOME, 102);
        m(KeyEvent.KEYCODE_DPAD_UP, 103);
        m(KeyEvent.KEYCODE_PAGE_UP, 104);
        m(KeyEvent.KEYCODE_DPAD_LEFT, 105);
        m(KeyEvent.KEYCODE_DPAD_RIGHT, 106);
        m(KeyEvent.KEYCODE_MOVE_END, 107);
        m(KeyEvent.KEYCODE_DPAD_DOWN, 108);
        m(KeyEvent.KEYCODE_PAGE_DOWN, 109);
        m(KeyEvent.KEYCODE_INSERT, 110);
        m(KeyEvent.KEYCODE_FORWARD_DEL, 111);
        m(KeyEvent.KEYCODE_VOLUME_MUTE, 113);
        m(KeyEvent.KEYCODE_NUMPAD_EQUALS, 117);
        m(KeyEvent.KEYCODE_BREAK, 119);
        m(KeyEvent.KEYCODE_META_LEFT, 125);
        m(KeyEvent.KEYCODE_META_RIGHT, 126);
        m(KeyEvent.KEYCODE_MENU, 127);
        m(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, 164);
        m(KeyEvent.KEYCODE_MEDIA_NEXT, 163);
        m(KeyEvent.KEYCODE_MEDIA_PREVIOUS, 165);
    }

    /** The Linux key code, or 0 if this key isn't sent. */
    static int linux(int androidCode) {
        return MAP.get(androidCode, 0);
    }

    private static final String PLAIN = "1234567890-=qwertyuiop[]asdfghjkl;'`\\zxcvbnm,./ ";
    private static final String SHIFTED = "!@#$%^&*()_+QWERTYUIOP{}ASDFGHJKL:\"~|ZXCVBNM<>? ";
    private static final int[] CODES = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24,
            25, 26, 27, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53,
            57};

    /** A typed character as {key, shift (0/1)}, or null if a US keyboard can't type it. */
    static int[] forChar(char c) {
        if (c == '\n') {
            return new int[]{KEY_ENTER, 0};
        }
        if (c == '\t') {
            return new int[]{15, 0};
        }
        int i = PLAIN.indexOf(c);
        if (i >= 0) {
            return new int[]{CODES[i], 0};
        }
        i = SHIFTED.indexOf(c);
        if (i >= 0) {
            return new int[]{CODES[i], 1};
        }
        return null;
    }
}
