package com.madrix.rig;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.midi.MidiDevice;
import android.media.midi.MidiDeviceInfo;
import android.media.midi.MidiManager;
import android.media.midi.MidiOutputPort;
import android.media.midi.MidiReceiver;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;

/**
 * Rig display. The phone is set to USB MIDI mode, so the PC sees it as a MIDI device; bridge.py
 * sends the display state to it as SysEx: F0 7D 'S' <JSON, ASCII> F7 (see display.snapshot_sysex).
 * The bridge resends every 2 s, so a silent link for longer than STALE_MS means it has stopped.
 */
public class MainActivity extends Activity {
    private static final int SYSEX_ID = 0x7D;
    private static final int SNAPSHOT_KIND = 'S';
    private static final long STALE_MS = 5000;

    private static final int BG = 0xFF0B0B0D;
    private static final int FG = 0xFFF2F2F2;
    private static final int DIM = 0xFF8A8A90;
    private static final int ACCENT = 0xFFFFB000;
    private static final int LINE = 0xFF26262A;
    private static final int OK = 0xFF33CC33;
    private static final int BAD = 0xFFFF5555;

    private final Handler main = new Handler(Looper.getMainLooper());
    private MidiManager midi;
    private MidiDevice device;
    private MidiOutputPort port;
    private long lastSnapshot;

    private View colorBar;
    private TextView page, pageInfo, track, pinned, value, controllers, link;

    // SysEx can arrive split across several onSend calls: collect until F7
    private final ByteArrayOutputStream sysex = new ByteArrayOutputStream();
    private boolean inSysex;

    private final MidiReceiver receiver = new MidiReceiver() {
        @Override
        public void onSend(byte[] data, int offset, int count, long timestamp) {
            for (int i = offset; i < offset + count; i++) {
                int b = data[i] & 0xFF;
                if (b == 0xF0) {
                    sysex.reset();
                    inSysex = true;
                } else if (b == 0xF7) {
                    if (inSysex) handleSysex(sysex.toByteArray());
                    inSysex = false;
                } else if (b >= 0xF8) {
                    // real-time bytes (clock etc.) may sit inside SysEx: skip them
                } else if (b >= 0x80) {
                    inSysex = false;  // any other status byte ends a broken SysEx
                } else if (inSysex) {
                    sysex.write(b);
                }
            }
        }
    };

    @SuppressWarnings("deprecation")
    private final MidiManager.DeviceCallback deviceCallback = new MidiManager.DeviceCallback() {
        @Override
        public void onDeviceAdded(MidiDeviceInfo info) {
            connect();
        }

        @Override
        public void onDeviceRemoved(MidiDeviceInfo info) {
            if (device != null && device.getInfo().getId() == info.getId()) {
                disconnect();
                showLink("USB MIDI disconnected", false);
            }
        }
    };

    private final Runnable staleCheck = new Runnable() {
        @Override
        public void run() {
            if (device != null && System.currentTimeMillis() - lastSnapshot > STALE_MS) {
                showLink("Connected, no data: is bridge.py running?", false);
            }
            main.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildLayout());
        // The camera cutout band above the content: same color as the page, not black
        getWindow().getDecorView().setBackgroundColor(BG);
        hideSystemBars();
        midi = getSystemService(MidiManager.class);
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onStart() {
        super.onStart();
        midi.registerDeviceCallback(deviceCallback, main);
        connect();
        main.post(staleCheck);
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onStop() {
        main.removeCallbacks(staleCheck);
        midi.unregisterDeviceCallback(deviceCallback);
        disconnect();
        super.onStop();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    // ---------- MIDI ----------

    @SuppressWarnings("deprecation")
    private void connect() {
        if (device != null) return;
        for (MidiDeviceInfo info : midi.getDevices()) {
            // The phone's own USB peripheral port: a USB device without a UsbDevice attached
            // (devices plugged INTO the phone carry one)
            boolean peripheral = info.getType() == MidiDeviceInfo.TYPE_USB
                    && info.getProperties().getParcelable(MidiDeviceInfo.PROPERTY_USB_DEVICE) == null;
            if (peripheral && info.getOutputPortCount() > 0) {
                open(info);
                return;
            }
        }
        showLink("Plug into the PC, tap the USB notification, choose MIDI", false);
    }

    private void open(MidiDeviceInfo info) {
        midi.openDevice(info, opened -> {
            if (opened == null) {
                showLink("Could not open USB MIDI", false);
                return;
            }
            device = opened;
            port = opened.openOutputPort(0);
            if (port == null) {
                showLink("USB MIDI port busy", false);
                return;
            }
            port.connect(receiver);
            lastSnapshot = 0;
            showLink("USB MIDI connected, waiting for bridge", false);
        }, main);
    }

    private void disconnect() {
        try {
            if (port != null) port.close();
            if (device != null) device.close();
        } catch (IOException ignored) {
            // closing anyway
        }
        port = null;
        device = null;
    }

    private void handleSysex(byte[] data) {
        if (data.length < 2 || data[0] != SYSEX_ID || data[1] != SNAPSHOT_KIND) return;
        String json = new String(data, 2, data.length - 2, StandardCharsets.US_ASCII);
        main.post(() -> {
            try {
                render(new JSONObject(json));
                lastSnapshot = System.currentTimeMillis();
                showLink("Live", true);
            } catch (JSONException e) {
                // a corrupt message: the next heartbeat replaces it
            }
        });
    }

    // ---------- display ----------

    private void render(JSONObject s) throws JSONException {
        String pageName = s.optString("page", "device");
        page.setText(pageName.toUpperCase());
        switch (pageName) {
            case "split": pageInfo.setText("Knobs 1-4 selected, 5-8 pinned"); break;
            case "link": pageInfo.setText("Each knob moves both tracks"); break;
            case "mix": pageInfo.setText("Knob N = track N, " + s.optString("mix_param")); break;
            default: pageInfo.setText("Knobs 1-8: selected track");
        }
        track.setText(orDash(s.optString("track")));
        pinned.setText(orDash(s.optString("pinned")));
        value.setText(s.optString("value").replace("|", "  ·  "));
        try {
            colorBar.setBackground(rounded(Color.parseColor(s.optString("color", "#333333")), 0));
        } catch (IllegalArgumentException ignored) {
            // keep the previous color
        }

        StringBuilder text = new StringBuilder();
        JSONObject connected = s.optJSONObject("controllers");
        if (connected != null) {
            for (Iterator<String> names = connected.keys(); names.hasNext(); ) {
                String name = names.next();
                text.append(connected.getBoolean(name) ? "● " : "○ ").append(name).append("    ");
            }
        }
        controllers.setText(text.toString().trim());
    }

    private void showLink(String message, boolean ok) {
        link.setText(message);
        link.setTextColor(ok ? OK : BAD);
    }

    private static String orDash(String text) {
        return text == null || text.isEmpty() ? "-" : text;
    }

    // ---------- layout, built in code (one screen, no resources to keep in sync) ----------

    // Landscape: knob page on the left, tracks and last change on the right, status along the bottom
    private View buildLayout() {
        LinearLayout root = column();
        root.setBackgroundColor(BG);
        int pad = dp(16);
        root.setPadding(pad, pad, pad, pad);

        colorBar = new View(this);
        colorBar.setBackground(rounded(0xFF333333, 0));
        root.addView(colorBar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(8)));

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.HORIZONTAL);

        LinearLayout left = column();
        left.addView(label("Knob page"));
        page = text(72, ACCENT, true);
        left.addView(page);
        pageInfo = label("");
        left.addView(pageInfo);
        body.addView(left, weighted(0, 1));

        LinearLayout right = column();
        LinearLayout tracks = new LinearLayout(this);
        tracks.setOrientation(LinearLayout.HORIZONTAL);
        track = text(28, FG, true);
        pinned = text(28, FG, true);
        tracks.addView(card("Selected (knobs 1-4 in split)", track), weighted(0, 1));
        tracks.addView(card("Pinned (knobs 5-8 in split)", pinned), weighted(dp(12), 1));
        right.addView(tracks);
        value = text(22, FG, false);
        right.addView(card("Last change", value), spaced());
        body.addView(right, weighted(dp(20), 1.6f));

        LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1);
        bodyParams.topMargin = dp(14);
        root.addView(body, bodyParams);

        LinearLayout status = new LinearLayout(this);
        status.setOrientation(LinearLayout.HORIZONTAL);
        controllers = label("");
        link = label("Starting");
        link.setGravity(Gravity.END);
        status.addView(controllers, weighted(0, 1));
        status.addView(link, weighted(dp(12), 1));
        root.addView(status);
        return root;
    }

    private LinearLayout card(String title, TextView content) {
        LinearLayout card = column();
        card.setBackground(rounded(BG, LINE));
        card.setPadding(dp(12), dp(12), dp(12), dp(12));
        card.addView(label(title));
        card.addView(content);
        return card;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private TextView label(String text) {
        TextView view = text(13, DIM, false);
        view.setText(text.toUpperCase());
        view.setLetterSpacing(0.08f);
        return view;
    }

    private TextView text(float sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        view.setGravity(Gravity.START);
        if (bold) view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(dp(10));
        if (stroke != 0) shape.setStroke(dp(1), stroke);
        return shape;
    }

    private LinearLayout.LayoutParams spaced() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(14);
        return params;
    }

    private LinearLayout.LayoutParams weighted(int leftMargin, float weight) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, weight);
        params.leftMargin = leftMargin;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @SuppressWarnings("deprecation")
    private void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.systemBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
        }
    }
}
