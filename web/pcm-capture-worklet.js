class AmoraPcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs?.[0]?.[0];
    if (channel && channel.length) {
      this.port.postMessage(channel.slice());
    }
    return true;
  }
}

registerProcessor("amora-pcm-capture", AmoraPcmCapture);
