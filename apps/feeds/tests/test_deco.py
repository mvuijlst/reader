"""The art-deco backdrops for magazine posts without a picture."""
import importlib.util
import xml.etree.ElementTree as ET
from collections import Counter

from django.conf import settings
from django.test import SimpleTestCase

from apps.feeds.templatetags.reader_tags import DECO_PATTERNS, DECO_TINTS, deco

STATIC = settings.BASE_DIR / "static"


def load_generator():
    path = settings.BASE_DIR / "tools" / "make_patterns.py"
    spec = importlib.util.spec_from_file_location("make_patterns", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DecoTests(SimpleTestCase):
    def test_stable_valid_and_well_spread(self):
        self.assertEqual(deco(42), deco(42))
        pattern, tint = deco(42).split()
        self.assertIn(pattern.removeprefix("pat-"), DECO_PATTERNS)
        self.assertIn(int(tint.removeprefix("tint-")), range(DECO_TINTS))

        picks = [deco(pk) for pk in range(1, 3001)]
        # Neighbouring posts (consecutive ids) almost never look identical.
        same = sum(1 for a, b in zip(picks, picks[1:]) if a == b)
        self.assertLess(same, 3000 / (len(DECO_PATTERNS) * DECO_TINTS) * 2)
        # Every pattern and tint gets used, roughly evenly.
        for counts, n in ((Counter(p.split()[0] for p in picks), len(DECO_PATTERNS)),
                          (Counter(p.split()[1] for p in picks), DECO_TINTS)):
            self.assertEqual(len(counts), n)
            self.assertGreater(min(counts.values()), 3000 / n * 0.75)

    def test_every_pattern_has_a_tile_and_css(self):
        css = (STATIC / "css" / "reader.css").read_text(encoding="utf-8")
        for name in DECO_PATTERNS:
            tile = STATIC / "img" / "patterns" / f"{name}.svg"
            root = ET.parse(tile).getroot()  # valid XML
            self.assertTrue(root.tag.endswith("svg"))
            self.assertIn(f".pat-{name} {{", css)
            self.assertIn(f"/static/img/patterns/{name}.svg", css)
        for n in range(DECO_TINTS):
            self.assertIn(f".tint-{n} {{", css)

    def test_tiles_match_the_generator(self):
        generator = load_generator()
        self.assertEqual(set(generator.PATTERNS), set(DECO_PATTERNS))
        for name, make in generator.PATTERNS.items():
            tile = (STATIC / "img" / "patterns" / f"{name}.svg").read_text(encoding="utf-8")
            self.assertEqual(tile, make(), f"{name}.svg is stale: run tools/make_patterns.py")
