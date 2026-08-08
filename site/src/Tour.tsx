import { useEffect, useState } from "react";

const asset = (name: string) => `${import.meta.env.BASE_URL}${name}`;

/* Each dot sits at a percentage of the screenshot's width and height, so
   the coordinates survive resizing. When the screenshot is replaced,
   re-aim these against the new image; nothing else cares. */
interface Hotspot {
  id: string;
  x: number;
  y: number;
  title: string;
  body: string;
}

const HOTSPOTS: Hotspot[] = [
  {
    id: "map",
    x: 12.5,
    y: 66,
    title: "Stage mapping",
    body: "darlaston watches the view and maps where you have been; this slide has 29 fields tracked. Pin a find to be guided back to it, or start a mosaic and every capture becomes a tile, placed where it was taken.",
  },
  {
    id: "calibration",
    x: 92,
    y: 6,
    title: "Calibration",
    body: "Dark, flat, white balance, and preview profile, kept per configuration. Missing pieces are a nag, not a gate: capture anyway, and the file records what it did and did not have.",
  },
  {
    id: "exposure",
    x: 92,
    y: 15,
    title: "Exposure assist",
    body: "A live histogram with a clipping readout that reads 0.00% when nothing burns. Exposure time and gain sit right below it, in real units.",
  },
  {
    id: "focus-rail",
    x: 92,
    y: 36,
    title: "Focus stacking",
    body: "Arm stack, then rack the fine focus and pause; a slice is taken, and the knob is the whole interface. Peak highlights sharp edges, and sweep reports when every region has been through focus.",
  },
  {
    id: "focus-assist",
    x: 22.8,
    y: 27.7,
    title: "Focus assist",
    body: "Sharpness is measured inside the marked region and traced over time, so critical focus has a number to lean on instead of an eyeball and a hunch.",
  },
  {
    id: "subject",
    x: 92,
    y: 74.8,
    title: "Provenance",
    body: "Subject, slide, optics, and photographer are written into every capture's EXIF. This one: a pseudoscorpion, on a slide mounted by Darlaston himself.",
  },
  {
    id: "capture",
    x: 91.3,
    y: 94,
    title: "12-bit RAW",
    body: "One press writes a 12-bit DNG with real headroom for Lightroom or Darktable, plus a JPEG that agrees with it. Averaged bursts are there when you want quieter shadows.",
  },
];

/* A dot can be linked directly, as /#tour-map. The prefix keeps these
   clear of the section anchors. */
function fromHash(): Hotspot | null {
  const m = window.location.hash.match(/^#tour-(.+)$/);
  return m ? (HOTSPOTS.find((h) => h.id === m[1]) ?? null) : null;
}

export default function Tour() {
  const [active, setActive] = useState<Hotspot | null>(fromHash);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActive(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  return (
    <div>
      <div className="tour" onClick={() => setActive(null)}>
        <img
          src={asset("darlaston_ui.jpg")}
          alt="The darlaston interface: live view of a pseudoscorpion slide with stage map, histogram, and capture controls"
        />
        {HOTSPOTS.map((h) => (
          <button
            key={h.id}
            className={`tour-dot${active?.id === h.id ? " active" : ""}`}
            style={{ left: `${h.x}%`, top: `${h.y}%` }}
            aria-label={h.title}
            onClick={(e) => {
              e.stopPropagation();
              setActive(active?.id === h.id ? null : h);
            }}
          />
        ))}
        {active && (
          <div
            className="tour-card"
            role="status"
            onClick={(e) => e.stopPropagation()}
            style={{
              top: `clamp(12px, calc(${active.y}% - 40px), calc(100% - 170px))`,
              ...(active.x > 55
                ? { right: `calc(${100 - active.x}% + 28px)` }
                : { left: `calc(${active.x}% + 28px)` }),
            }}
          >
            <div className="tour-card-title">{active.title}</div>
            <p>{active.body}</p>
          </div>
        )}
      </div>
      <p className="tour-hint">Click a dot to look closer.</p>
    </div>
  );
}
