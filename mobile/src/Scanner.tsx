import { useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader, IScannerControls } from "@zxing/browser";

// Continuous barcode scanning with the back camera. Calls onCode once per scan; the same code is ignored for 1.2 s so
// holding the phone over one item does not count it twice. Works in Chrome / Safari and inside the Android / iOS shells.
export default function Scanner({ onCode, onClose }: { onCode: (code: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState("");
  const [last, setLast] = useState("");
  useEffect(() => {
    let controls: IScannerControls | undefined;
    let recent = { code: "", at: 0 };
    const reader = new BrowserMultiFormatReader();
    reader
      .decodeFromConstraints({ video: { facingMode: { ideal: "environment" } } }, video.current!, (res) => {
        if (!res) return;
        const code = res.getText().trim();
        const now = Date.now();
        if (code === recent.code && now - recent.at < 1200) return;
        recent = { code, at: now };
        setLast(code);
        if (navigator.vibrate) navigator.vibrate(40);
        onCode(code);
      })
      .then((c) => { controls = c; })
      .catch((e) => setErr(e?.name === "NotAllowedError" ? "Camera permission was refused. Allow it in the phone's settings." : `Camera not available (${e?.name || e})`));
    return () => controls?.stop();
  }, [onCode]);
  return (
    <div className="scanner">
      <video ref={video} playsInline muted />
      <div className="aim" />
      <div className="scan-bar">
        <span>{err || (last ? `Last: ${last}` : "Point at a barcode")}</span>
        <button className="btn light" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}
