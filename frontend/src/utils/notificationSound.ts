/**
 * Play a short notification ping when agent work completes or user input is needed.
 * Uses Web Audio API — no external sound file required.
 */

let audioContext: AudioContext | null = null;

/**
 * Prepare audio output after a user gesture (browser autoplay policy).
 */
export function unlockNotificationAudio(): void {
  try {
    if (!audioContext) {
      audioContext = new AudioContext();
    }
    if (audioContext.state === "suspended") {
      void audioContext.resume();
    }
  } catch {
    // Audio not available in this environment.
  }
}

/**
 * Play a short two-tone ping.
 */
export function playNotificationPing(): void {
  try {
    if (!audioContext) {
      audioContext = new AudioContext();
    }

    const ctx = audioContext;
    if (ctx.state === "suspended") {
      void ctx.resume();
    }

    const playTone = (frequency: number, startOffset: number, duration: number) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      oscillator.connect(gain);
      gain.connect(ctx.destination);

      const start = ctx.currentTime + startOffset;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.12, start + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

      oscillator.start(start);
      oscillator.stop(start + duration + 0.02);
    };

    playTone(880, 0, 0.12);
    playTone(1318.5, 0.1, 0.18);
  } catch {
    // Ignore playback errors (muted tab, missing audio device, etc.).
  }
}
