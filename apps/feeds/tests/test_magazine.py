from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.feeds.services.magazine import layout


def items(pictures):
    """Fake items numbered 0..n; `pictures` says which have a thumbnail ('x' yes, '.' no)."""
    return [SimpleNamespace(n=i, thumbnail="pic" if c == "x" else "") for i, c in enumerate(pictures)]


def cells(placed):
    """Grid cells used in the 3-column layout (hero 4, wide 2, small 1)."""
    return sum({"hero": 4, "wide": 2}.get(i.mag_slot, 1) for i in placed)


class MagazineLayoutTests(SimpleTestCase):
    def test_cycle_with_pictures_everywhere(self):
        placed, consumed, block = layout(items("x" * 14), limit=14)
        self.assertEqual([i.mag_slot for i in placed],
                         ["hero", "", "", "", "", "", "", "", "", "wide", "", "", "", ""])
        self.assertEqual((consumed, block), (14, 5))
        self.assertEqual([i.n for i in placed], list(range(14)))  # order kept

    def test_featured_item_must_have_a_picture(self):
        # Hero block 0..2: only #2 has a picture, so it moves to the front of its block.
        placed, _, _ = layout(items("..x" + "x" * 11), limit=14)
        self.assertEqual([(i.n, i.mag_slot) for i in placed[:3]], [(2, "hero"), (0, ""), (1, "")])

    def test_blocks_without_pictures_become_rows(self):
        placed, consumed, _ = layout(items("." * 15), limit=15)
        self.assertTrue(all(i.mag_slot == "" for i in placed))
        self.assertEqual(consumed, 15)
        # A wide block with no picture takes three items, not two, so rows stay whole.
        self.assertEqual(cells(placed) % 3, 0)

    def test_every_block_fills_whole_rows(self):
        for pattern in ("x" * 40, "." * 40, "x.." * 14, ".x" * 20, "..x." * 10):
            placed, consumed, _ = layout(items(pattern), limit=30)
            self.assertEqual(cells(placed) % 3, 0, pattern)
            self.assertGreaterEqual(len(placed), 30)
            self.assertLessEqual(len(placed), 33)

    def test_pages_continue_without_losing_or_repeating_items(self):
        stream = items(("x." * 50)[:100])
        seen, start, block = [], 0, 0
        while start < len(stream):
            page = stream[start:start + 34]  # what the view fetches: page size + 4
            placed, consumed, block = layout(page, block, limit=30)
            seen += [i.n for i in placed]
            start += consumed
        self.assertEqual(sorted(seen), list(range(100)))
        self.assertEqual(len(seen), len(set(seen)))
