import { useEffect, useState } from "react";

const REPO = "nsfm/darlaston";
const RELEASES = `https://github.com/${REPO}/releases`;

interface ReleaseAsset {
  name: string;
  browser_download_url: string;
  size: number;
}

interface Release {
  tag_name: string;
  html_url: string;
  published_at: string;
  assets: ReleaseAsset[];
}

/* Artifact filenames carry a platform label from packaging/bundle.py;
   match on it rather than parsing the whole name. */
const PLATFORMS = [
  { label: "windows-x86_64", platform: "Windows", detail: "x86_64" },
  { label: "macos-arm64", platform: "Mac OS", detail: "Apple silicon" },
  { label: "macos-x86_64", platform: "Mac OS", detail: "Intel" },
  { label: "linux-x86_64", platform: "Linux", detail: "x86_64" },
];

function fmtSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}

export default function Downloads() {
  const [release, setRelease] = useState<Release | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: Release) => setRelease(data))
      .catch(() => setFailed(true));
  }, []);

  if (failed || (release && release.assets.length === 0)) {
    return (
      <p className="dl-meta">
        Grab the latest build from the{" "}
        <a href={RELEASES}>releases page on GitHub</a> and follow the
        installation instructions for your platform.
      </p>
    );
  }

  if (!release) {
    return <p className="dl-meta">Checking the latest release…</p>;
  }

  const cards = PLATFORMS.flatMap((p) => {
    const asset = release.assets.find((a) => a.name.includes(p.label));
    return asset ? [{ ...p, asset }] : [];
  });

  return (
    <>
      <div className="downloads-grid">
        {cards.map(({ label, platform, detail, asset }) => (
          <a key={label} className="dl-card" href={asset.browser_download_url}>
            <div className="platform">{platform}</div>
            <div className="detail">
              {detail} · {fmtSize(asset.size)}
            </div>
          </a>
        ))}
      </div>
      <p className="dl-meta">
        {release.tag_name} · built{" "}
        {new Date(release.published_at).toLocaleDateString("en-US", {
          year: "numeric",
          month: "long",
          day: "numeric",
        })}{" "}
        · <a href={release.html_url}>release notes</a> ·{" "}
        <a href={RELEASES}>all releases</a>
      </p>
    </>
  );
}
