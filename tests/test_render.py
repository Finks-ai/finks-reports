import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import render  # noqa: E402
import scan  # noqa: E402

MODEL = ROOT / "tests" / "model.json"
EXPECTED = ROOT / "tests" / "expected"


def model() -> dict:
    return json.loads(MODEL.read_text())


def build(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    render.write(render.site(model()), out)
    return out


def test_renders_byte_identical(tmp_path):
    out = build(tmp_path)
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert files == sorted(scan.FILES)
    for rel in scan.FILES:
        assert (out / rel).read_bytes() == (EXPECTED / rel).read_bytes()


def test_scan_passes(tmp_path):
    scan.scan(build(tmp_path))


@pytest.mark.parametrize(
    "planted",
    [
        "account 123456789012",
        "s3://bucket/key",
        "cost $12",
        "user@example.com",
        "see #456",
        "symbol_daily",
        "api.example.com",
        "AKIAABCDEFGHIJKLMNOP",
        "<script>alert(1)</script>",
        '<a href="https://example.com/">x</a>',
        '<img src="x">',
        '<p onclick="x">x</p>',
        "<!-- note -->",
        "s3:<b></b>//bucket",  # split across tags
        "\uff53\uff13://bucket",  # fullwidth
        "s3:\u200b//bucket",  # zero-width space
        "<![CDATA[ck]]>",
        "<!ENTITY x 'y'>",
    ],
)
def test_scan_fails_on_planted_secret(tmp_path, planted):
    out = build(tmp_path)
    page = out / "evals/index.html"
    page.write_text(page.read_text().replace("</main>", f"<p>{planted}</p></main>"))
    with pytest.raises(scan.Leak):
        scan.scan(out)
    assert planted not in str(pytest.raises(scan.Leak, scan.scan, out).value)


@pytest.mark.parametrize(
    ("planted", "rule"),
    [
        ("<p>12 GB</p>", "storage size"),
        ("<p>1.8 terabytes</p>", "storage size"),
        ('<p aria-label="720GB">x</p>', "storage size"),
        ("<p>2.5 million tokens</p>", "token word"),
    ],
)
def test_sizes_and_tokens_only_on_the_lake_page(tmp_path, planted, rule):
    out = build(tmp_path)
    for rel in (
        "evals/index.html",
        "costs/index.html",
        "roadmap/index.html",
        "index.html",
        "private/index.html",
    ):
        page = out / rel
        text = page.read_text()
        page.write_text(text.replace("</main>", f"{planted}</main>"))
        with pytest.raises(scan.Leak, match=rule):
            scan.scan(out)
        page.write_text(text)
    lake = out / "lake/index.html"
    lake.write_text(lake.read_text().replace("</main>", f"{planted}</main>"))
    scan.scan(out)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("</style>", "/* ck_dev */</style>"),  # CSS comment
        ("</style>", ".x::after{content:'secret'}</style>"),
        ("</style>", ".x{color:#111}.y{background:url(x)}</style>"),
    ],
)
def test_scan_fails_on_css(tmp_path, old, new):
    out = build(tmp_path)
    page = out / "costs/index.html"
    page.write_text(page.read_text().replace(old, new, 1))
    with pytest.raises(scan.Leak):
        scan.scan(out)


def test_scan_fails_on_extra_file_or_symlink(tmp_path):
    out = build(tmp_path)
    (out / "costs/data.json").write_text("{}")
    with pytest.raises(scan.Leak, match="unexpected file"):
        scan.scan(out)
    (out / "costs/data.json").unlink()
    (out / "evals/link").symlink_to(tmp_path)
    with pytest.raises(scan.Leak, match="symlink"):
        scan.scan(out)


@pytest.mark.parametrize("version", [2, None])
def test_wrong_version_refused(tmp_path, version):
    m = model()
    if version is None:
        del m["version"]
    else:
        m["version"] = version
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(m))
    out = tmp_path / "site"
    with pytest.raises(SystemExit, match="version"):
        render.main(["--model", str(bad), "--out", str(out)])
    assert not out.exists()


def test_write_deletes_output_on_leak(tmp_path):
    files = render.site(model())
    files["index.html"] = files["index.html"].replace("</main>", "<p>s3://x</p></main>")
    with pytest.raises(scan.Leak):
        render.write(files, tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_cli_deletes_output_on_leak(tmp_path, monkeypatch):
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(model()))
    out = tmp_path / "site"

    original = render.site

    def leaky_site(m):
        files = original(m)
        files["index.html"] = files["index.html"].replace("</main>", "<p>s3://x</p></main>")
        return files

    monkeypatch.setattr(render, "site", leaky_site)
    with pytest.raises(SystemExit, match="refused"):
        render.main(["--model", str(model_path), "--out", str(out)])
    assert not out.exists()


def test_write_refuses_to_replace_a_foreign_dir(tmp_path):
    (tmp_path / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        render.write(render.site(model()), tmp_path)
    assert (tmp_path / "notes.txt").exists()
