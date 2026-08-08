import Downloads from "./Downloads";
import Tour from "./Tour";

const GITHUB = "https://github.com/nsfm/darlaston";

/* public/ assets need the base prefix by hand in JSX. */
const asset = (name: string) => `${import.meta.env.BASE_URL}${name}`;

const FEATURES = [
  {
    title: "12-bit RAW",
    body: "The only photomicrography tool that gives you RAW photos with extended dynamic range, ready for Lightroom, Darktable, or any postprocessing app.",
  },
  {
    title: "Focus stacking",
    body: "An easy workflow that detects focus changes and takes shots automatically as you rack through your specimen.",
  },
  {
    title: "Mosaic stitching",
    body: "Capture super high resolution images larger than your field of view.",
  },
  {
    title: "Focus stacked mosaics",
    body: "Combine stacking and stitching in a single pass, seamlessly. No other tool does this.",
  },
  {
    title: "Stage mapping",
    body: "Tracks your movements and builds a live minimap of your slide to help you plan captures.",
  },
  {
    title: "Comprehensive EXIF",
    body: "Optics tagging keeps your work properly documented, including subject metadata and author copyrights.",
  },
  {
    title: "Focus and exposure assist",
    body: "Sharpness tracking and a detailed histogram help you capture the best shots possible.",
  },
  {
    title: "Scale bars",
    body: "Calculate the size of your specimens based on your current optical setup.",
  },
  {
    title: "Timelapses",
    body: "See how your specimen changes over time.",
  },
  {
    title: "Creative depthworks",
    body: "Turn focus stacks into wigglegrams, focus pulls, turntables, stereo pairs, anaglyphs, autostereograms, printable meshes, and more.",
  },
];

type Cell = { text: string; cls?: string };
const yes: Cell = { text: "Yes", cls: "yes" };
const no: Cell = { text: "No", cls: "no" };

const COMPARISON: { row: string; cells: Cell[] }[] = [
  {
    row: "Runs on",
    cells: [
      { text: "Mac OS, Windows, Linux" },
      { text: "Windows" },
      { text: "Mac OS, Windows" },
      { text: "Mac OS, Windows, Linux" },
    ],
  },
  {
    row: "Cost",
    cells: [
      { text: "Free", cls: "yes" },
      { text: "Free", cls: "yes" },
      { text: "$200", cls: "cost" },
      { text: "$89 to $289", cls: "cost" },
    ],
  },
  { row: "Mosaic stitching", cells: [yes, yes, yes, no] },
  { row: "Focus stacking", cells: [yes, yes, yes, yes] },
  { row: "Stack + stitch", cells: [yes, no, no, no] },
  {
    row: "RAW output",
    cells: [yes, no, { text: "Yes (plugins)", cls: "partial" }, no],
  },
  {
    row: "Camera control",
    cells: [yes, yes, { text: "Yes (+$75)", cls: "partial" }, no],
  },
  { row: "Measurements", cells: [yes, yes, no, no] },
  {
    row: "Stage mapping",
    cells: [yes, { text: "Partial", cls: "partial" }, no, no],
  },
  { row: "Depth mapping", cells: [yes, no, yes, yes] },
];

export default function App() {
  return (
    <>
      <header>
        <div className="container">
          <img src={asset("mark.png")} alt="" />
          <nav>
            <a href="#features">features</a>
            <a href="#compare">compare</a>
            <a href="#download">download</a>
            <a href={GITHUB}>github</a>
          </nav>
        </div>
      </header>

      <main>
        <div className="hero container">
          <img
            className="wordmark"
            src={asset("wordmark.png")}
            alt="darlaston"
          />
          <h1>A free, open source tool for creative microscopy.</h1>
          <p className="sub">
            Focus stacking, mosaic stitching, 12-bit RAW capture, and a live
            map of your slide. Built for photographing diatom arrangements,
            made for sharing your love of the microscopic world.
          </p>
          <div className="cta-row">
            <a className="btn primary" href="#download">
              Download
            </a>
            <a className="btn" href={GITHUB}>
              View source
            </a>
          </div>
          <div className="screenshot">
            <Tour />
          </div>
        </div>

        <section id="story">
          <div className="container">
            <div className="kicker">Every slide perfect</div>
            <p className="story">
              Named for <strong>Herbert William Hutton Darlaston</strong>{" "}
              (1867 to 1949), who prepared nearly a thousand microscope slides
              in his first year at the bench, only to give most of them away.
              He continued his work for the next fifty years under the promise{" "}
              <em className="promise">Every Slide Perfect</em>. This tool
              carries the same spirit: careful work, freely shared.
            </p>
          </div>
        </section>

        <section id="features">
          <div className="container">
            <div className="kicker">Features</div>
            <h2>A full suite of photomicrography utilities</h2>
            <div className="features-grid">
              {FEATURES.map((f) => (
                <div className="feature" key={f.title}>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="compare">
          <div className="container">
            <div className="kicker">Compared to other tools</div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th className="us">darlaston</th>
                    <th>ToupView</th>
                    <th>Helicon</th>
                    <th>Zerene</th>
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON.map(({ row, cells }) => (
                    <tr key={row}>
                      <td>{row}</td>
                      {cells.map((c, i) => (
                        <td
                          key={i}
                          className={`${i === 0 ? "us " : ""}${c.cls ?? ""}`}
                        >
                          {c.text}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section id="download">
          <div className="container">
            <div className="kicker">Download</div>
            <h2>Mac OS, Windows, and Linux</h2>
            <Downloads />
            <p className="dl-meta" style={{ marginTop: 12 }}>
              Compatible with most USB microscope cameras, Toup family and
              generic alike. Prefer source? Clone the repo and run{" "}
              <code>make install</code>.
            </p>
          </div>
        </section>
      </main>

      <footer>
        <div className="container">
          <p>
            Free to use, share, and modify under the GPLv3, with a linking
            exception for the ToupTek SDK. No analytics, no cookies, no
            accounts. Downloads are served by GitHub.
          </p>
          <p>
            <a href={GITHUB}>Source</a> ·{" "}
            <a href={`${GITHUB}/releases`}>Releases</a> ·{" "}
            <a href={`${GITHUB}/issues`}>Issues</a> ·{" "}
            <a href={`${GITHUB}/blob/main/SUPPORT.md`}>Support</a>
          </p>
        </div>
      </footer>
    </>
  );
}
