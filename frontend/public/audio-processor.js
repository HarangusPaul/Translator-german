/**
 * AudioWorklet processor — converts Float32 mic input to Int16 PCM and
 * forwards it to the main thread. Performs in-worklet silence detection so
 * that a zero-length ArrayBuffer is posted after 200 ms of continuous silence
 * (RMS < 0.01), giving the backend an utterance boundary signal without
 * waiting for server-side VAD.
 */
class AudioChunkProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // 128 samples / 16 000 Hz ≈ 8 ms per process() call → 150 calls ≈ 1.2 s
    this._SILENCE_FRAMES = 150;
    this._RMS_THRESHOLD = 0.01;
    this._silenceFrames = 0;
    this._silenceSignalSent = false;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel?.length) return true;

    // Compute RMS energy for this block
    let sum = 0;
    for (let i = 0; i < channel.length; i++) sum += channel[i] * channel[i];
    const rms = Math.sqrt(sum / channel.length);

    if (rms >= this._RMS_THRESHOLD) {
      // ── Speech block ────────────────────────────────────────────────────
      this._silenceFrames = 0;
      this._silenceSignalSent = false;

      // Convert Float32 → Int16 and transfer (zero-copy) to main thread
      const int16 = new Int16Array(channel.length);
      for (let i = 0; i < channel.length; i++) {
        int16[i] = Math.max(-32768, Math.min(32767, channel[i] * 32767));
      }
      this.port.postMessage(int16.buffer, [int16.buffer]);
    } else {
      // ── Silence block ───────────────────────────────────────────────────
      this._silenceFrames++;

      // Post the silence marker exactly once per silence period
      if (
        this._silenceFrames >= this._SILENCE_FRAMES &&
        !this._silenceSignalSent
      ) {
        this.port.postMessage(new ArrayBuffer(0));
        this._silenceSignalSent = true;
      }
    }

    return true;
  }
}

registerProcessor("audio-chunk-processor", AudioChunkProcessor);
