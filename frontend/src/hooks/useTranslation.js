import { useCallback, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";

const DEFAULT_WS_URL = "ws://localhost:8000/ws";

/**
 * useTranslation(sessionId, direction, playTTS?)
 *
 * Opens a WebSocket to the backend, streams PCM audio from the user's mic or
 * tab, and returns live partial + accumulated final transcript/translation.
 *
 * @param {string|null} sessionId  Firestore session ID (null → no persistence)
 * @param {string}      direction  "de_to_en" | "en_to_de"
 * @param {boolean}     playTTS    Whether to play TTS audio received from server
 */
export default function useTranslation(sessionId, direction, playTTS = true) {
  const { getFreshToken } = useAuth();

  const [status, setStatus] = useState("idle"); // idle | connecting | live | error
  const [partialTranscript, setPartialTranscript] = useState("");
  const [finalTranscript, setFinalTranscript] = useState("");
  const [partialTranslation, setPartialTranslation] = useState("");
  const [finalTranslation, setFinalTranslation] = useState("");

  const wsRef = useRef(null);
  const audioCtxRef = useRef(null);
  const playCtxRef = useRef(null);
  const streamRef = useRef(null);
  const workletRef = useRef(null);

  // Stores the most recent final transcript so save_message can include it
  const pendingFinalRef = useRef({ transcript: "", translation: "" });

  // ── Audio playback ───────────────────────────────────────────────────────

  const playMP3 = useCallback(async (b64) => {
    try {
      if (!playCtxRef.current || playCtxRef.current.state === "closed") {
        playCtxRef.current = new AudioContext();
      }
      const ctx = playCtxRef.current;
      if (ctx.state === "suspended") await ctx.resume();

      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const buffer = await ctx.decodeAudioData(bytes.buffer);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(ctx.destination);
      src.start();
    } catch (err) {
      console.error("Playback error:", err);
    }
  }, []);

  // ── WebSocket message handler ────────────────────────────────────────────

  const handleMessage = useCallback(
    (msg) => {
      switch (msg.type) {
        case "transcript_partial":
          setPartialTranscript(msg.text);
          break;

        case "transcript_final":
          setFinalTranscript((prev) => (prev ? prev + "\n" + msg.text : msg.text));
          setPartialTranscript("");
          pendingFinalRef.current.transcript = msg.text;
          break;

        case "translation_partial":
          setPartialTranslation(msg.text);
          break;

        case "translation_final": {
          const translation = msg.text;
          setFinalTranslation((prev) => (prev ? prev + "\n" + translation : translation));
          setPartialTranslation("");

          // Persist via backend (server writes to Firestore on save_message)
          const ws = wsRef.current;
          if (ws?.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({
                type: "save_message",
                source_text: pendingFinalRef.current.transcript,
                translated_text: translation,
                direction,
              })
            );
          }
          pendingFinalRef.current = { transcript: "", translation: "" };
          break;
        }

        case "audio":
          if (playTTS) playMP3(msg.data);
          break;

        case "error":
          console.error("Server error:", msg.message);
          setStatus("error");
          break;

        default:
          break;
      }
    },
    [direction, playTTS, playMP3]
  );

  // ── Stream setup (shared by mic and tab-audio paths) ─────────────────────

  const startWithStream = useCallback(
    async (stream) => {
      if (!stream.getAudioTracks().length) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      streamRef.current = stream;
      setStatus("connecting");

      let token;
      try {
        token = await getFreshToken();
      } catch (err) {
        console.error("Auth error:", err);
        setStatus("error");
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      const wsUrl =
        (typeof import.meta !== "undefined" && import.meta.env?.VITE_WS_URL) ||
        DEFAULT_WS_URL;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.binaryType = "arraybuffer";

      ws.onopen = async () => {
        // Send init frame first
        ws.send(JSON.stringify({ token, session_id: sessionId, direction }));

        try {
          const ctx = new AudioContext({ sampleRate: 16_000 });
          audioCtxRef.current = ctx;
          if (ctx.state === "suspended") await ctx.resume();

          await ctx.audioWorklet.addModule("/audio-processor.js");

          const source = ctx.createMediaStreamSource(stream);
          const worklet = new AudioWorkletNode(ctx, "audio-chunk-processor");
          workletRef.current = worklet;

          worklet.port.onmessage = (e) => {
            // e.data is ArrayBuffer: non-empty = Int16 PCM, empty = silence marker
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(e.data);
            }
          };

          source.connect(worklet);
          setStatus("live");
        } catch (err) {
          console.error("Audio setup error:", err);
          setStatus("error");
        }
      };

      ws.onmessage = (e) => {
        try {
          handleMessage(JSON.parse(e.data));
        } catch {
          // ignore non-JSON frames
        }
      };

      ws.onclose = (e) => {
        wsRef.current = null;
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        audioCtxRef.current?.close().catch(() => {});
        audioCtxRef.current = null;

        if (e.code === 1006) {
          setStatus("error");
        } else {
          setStatus("idle");
        }
      };

      ws.onerror = () => {
        // onclose fires after with the real code — handled there
      };
    },
    [sessionId, direction, getFreshToken, handleMessage]
  );

  // ── Public controls ──────────────────────────────────────────────────────

  const start = useCallback(
    async (useTabAudio = false) => {
      try {
        let stream;
        if (useTabAudio) {
          stream = await navigator.mediaDevices.getDisplayMedia({
            audio: { echoCancellation: false, noiseSuppression: false },
            video: true,
          });
          // Chrome requires video:true but we don't need it
          stream.getVideoTracks().forEach((t) => t.stop());
        } else {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: true,
              noiseSuppression: true,
              sampleRate: 16_000,
            },
            video: false,
          });
        }
        await startWithStream(stream);
      } catch (err) {
        if (err.name !== "NotAllowedError") console.error("Start error:", err);
        setStatus("idle");
      }
    },
    [startWithStream]
  );

  const stop = useCallback(() => {
    // Kill audio capture immediately
    workletRef.current?.disconnect();
    workletRef.current = null;
    audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }
    setStatus("idle");
  }, []);

  const clear = useCallback(() => {
    setPartialTranscript("");
    setFinalTranscript("");
    setPartialTranslation("");
    setFinalTranslation("");
    pendingFinalRef.current = { transcript: "", translation: "" };
  }, []);

  return {
    status,
    partialTranscript,
    finalTranscript,
    partialTranslation,
    finalTranslation,
    start,
    stop,
    clear,
  };
}
