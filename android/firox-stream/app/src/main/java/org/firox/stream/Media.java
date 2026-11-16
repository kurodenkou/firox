package org.firox.stream;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.util.Log;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Hardware decoding of the host's picture (H.264) and sound (Opus). */
final class Media {
    static final String TAG = "FiroxStream";

    private Media() {
    }

    /**
     * The picture: H.264 access units in Annex B form (start codes), with the
     * SPS and PPS repeated before every key frame. The decoder starts at the
     * first key frame; frames before it are dropped (and one is asked for).
     */
    static final class Video {
        private final Surface surface;
        private MediaCodec codec;
        private int width = 1280, height = 720;
        private long pts;
        private final MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        boolean broken;

        Video(Surface surface) {
            this.surface = surface;
        }

        void setSize(int w, int h) {
            if (w > 0 && h > 0 && (w != width || h != height)) {
                width = w;
                height = h;
                release();                  // a new size: start again at the next key frame
            }
        }

        boolean running() {
            return codec != null;
        }

        /** Returns false while waiting for a key frame (the caller asks for one). */
        boolean feed(byte[] au) {
            if (broken) {
                return true;
            }
            if (codec == null && !start(au)) {
                return false;
            }
            try {
                int idx = codec.dequeueInputBuffer(20000);
                if (idx >= 0) {
                    ByteBuffer in = codec.getInputBuffer(idx);
                    in.clear();
                    if (au.length > in.capacity()) {
                        codec.queueInputBuffer(idx, 0, 0, pts, 0);
                    } else {
                        in.put(au);
                        codec.queueInputBuffer(idx, 0, au.length, pts, 0);
                    }
                    pts += 16666;
                }
                drain();
                return true;
            } catch (Exception e) {
                Log.w(TAG, "video decoder: " + e);
                release();
                return false;
            }
        }

        private void drain() {
            while (true) {
                int out = codec.dequeueOutputBuffer(info, 0);
                if (out >= 0) {
                    codec.releaseOutputBuffer(out, true);      // show it now
                } else if (out == MediaCodec.INFO_TRY_AGAIN_LATER) {
                    return;
                }
                // format or buffer changes: keep going
            }
        }

        private boolean start(byte[] au) {
            byte[][] ps = parameterSets(au);
            if (ps == null) {
                return false;
            }
            try {
                MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
                f.setByteBuffer("csd-0", ByteBuffer.wrap(ps[0]));
                f.setByteBuffer("csd-1", ByteBuffer.wrap(ps[1]));
                f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, Math.max(width * height, 1 << 20));
                if (Build.VERSION.SDK_INT >= 30) {
                    f.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
                }
                f.setInteger(MediaFormat.KEY_PRIORITY, 0);      // real time
                // Vendor hints some decoders honour for low latency
                f.setInteger("vendor.qti-ext-dec-low-latency.enable", 1);
                f.setInteger("vendor.low-latency.enable", 1);
                codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
                try {
                    codec.configure(f, surface, null, 0);
                } catch (Exception e) {
                    // Without the vendor keys, which some decoders reject
                    codec.release();
                    f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
                    f.setByteBuffer("csd-0", ByteBuffer.wrap(ps[0]));
                    f.setByteBuffer("csd-1", ByteBuffer.wrap(ps[1]));
                    f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, Math.max(width * height, 1 << 20));
                    codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
                    codec.configure(f, surface, null, 0);
                }
                codec.start();
                return true;
            } catch (Exception e) {
                Log.e(TAG, "can't start the video decoder: " + e);
                if (codec != null) {
                    codec.release();
                    codec = null;
                }
                broken = true;
                return false;
            }
        }

        void release() {
            if (codec != null) {
                try {
                    codec.stop();
                } catch (Exception ignored) {
                }
                codec.release();
                codec = null;
            }
        }

        /** The SPS and PPS (with their start codes) from a key frame, or null. */
        static byte[][] parameterSets(byte[] au) {
            byte[] sps = null, pps = null;
            int i = 0, n = au.length;
            while (i + 3 < n) {
                int start = nextStart(au, i);
                if (start < 0) {
                    break;
                }
                int hdr = start + (au[start + 2] == 1 ? 3 : 4);
                int end = nextStart(au, hdr);
                if (end < 0) {
                    end = n;
                }
                if (hdr < n) {
                    int type = au[hdr] & 0x1f;
                    if (type == 7 && sps == null) {
                        sps = slice(au, start, end);
                    } else if (type == 8 && pps == null) {
                        pps = slice(au, start, end);
                    }
                }
                i = end;
            }
            return sps != null && pps != null ? new byte[][]{sps, pps} : null;
        }

        private static int nextStart(byte[] b, int from) {
            for (int i = from; i + 3 < b.length; i++) {
                if (b[i] == 0 && b[i + 1] == 0 && (b[i + 2] == 1 || (b[i + 2] == 0 && b[i + 3] == 1))) {
                    return i;
                }
            }
            return -1;
        }

        private static byte[] slice(byte[] b, int from, int to) {
            byte[] out = new byte[to - from];
            System.arraycopy(b, from, out, 0, out.length);
            return out;
        }
    }

    /** The sound: raw Opus packets (48 kHz stereo, 10 ms), played with little buffering. */
    static final class Audio {
        private MediaCodec codec;
        private AudioTrack track;
        private final MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        private long pts;
        private int rate = 48000, channels = 2;
        private boolean broken;

        void setFormat(int r, int c) {
            if (r > 0) {
                rate = r;
            }
            if (c > 0) {
                channels = c;
            }
        }

        void feed(byte[] packet) {
            if (broken) {
                return;
            }
            try {
                if (codec == null) {
                    start();
                }
                int idx = codec.dequeueInputBuffer(5000);
                if (idx >= 0) {
                    ByteBuffer in = codec.getInputBuffer(idx);
                    in.clear();
                    in.put(packet);
                    codec.queueInputBuffer(idx, 0, packet.length, pts, 0);
                    pts += 10000;
                }
                while (true) {
                    int out = codec.dequeueOutputBuffer(info, 0);
                    if (out < 0) {
                        if (out == MediaCodec.INFO_TRY_AGAIN_LATER) {
                            break;
                        }
                        continue;
                    }
                    ByteBuffer pcm = codec.getOutputBuffer(out);
                    if (pcm != null && info.size > 0) {
                        pcm.position(info.offset);
                        pcm.limit(info.offset + info.size);
                        // Never wait for the sound card: a late packet is dropped
                        track.write(pcm, info.size, AudioTrack.WRITE_NON_BLOCKING);
                    }
                    codec.releaseOutputBuffer(out, false);
                }
            } catch (Exception e) {
                Log.w(TAG, "sound off: " + e);
                release();
                broken = true;
            }
        }

        private void start() throws Exception {
            MediaFormat f = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, rate, channels);
            // OpusHead, then pre-skip and seek pre-roll in nanoseconds, as Android wants them
            ByteBuffer head = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN);
            head.put("OpusHead".getBytes("US-ASCII"));
            head.put((byte) 1).put((byte) channels).putShort((short) 0).putInt(rate)
                    .putShort((short) 0).put((byte) 0);
            head.flip();
            f.setByteBuffer("csd-0", head);
            f.setByteBuffer("csd-1", ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(0, 0));
            f.setByteBuffer("csd-2", ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(0, 80000000L));
            codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
            codec.configure(f, null, null, 0);
            codec.start();
            int mask = channels == 1 ? AudioFormat.CHANNEL_OUT_MONO : AudioFormat.CHANNEL_OUT_STEREO;
            int min = AudioTrack.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT);
            track = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_GAME)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build())
                    .setAudioFormat(new AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(rate).setChannelMask(mask).build())
                    .setBufferSizeInBytes(Math.max(min, rate * channels * 2 / 10))   // ~100 ms at most
                    .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build();
            track.play();
        }

        void release() {
            if (codec != null) {
                try {
                    codec.stop();
                } catch (Exception ignored) {
                }
                codec.release();
                codec = null;
            }
            if (track != null) {
                try {
                    track.stop();
                } catch (Exception ignored) {
                }
                track.release();
                track = null;
            }
        }
    }
}
