"""Magazine view: arrange a stream of items into a collage of blocks.

The grid has three columns. Blocks repeat in this cycle, each filling whole rows:

    hero  - a 2x2 featured item, with two small items stacked beside it
    row   - three small items
    row
    wide  - a 2-column featured item next to one small item
    row

A featured slot needs an item with a picture. Within its block the first such
item is moved to the front; if the block has none, it becomes a plain row of
three. That way every block fills whole rows and the grid never has gaps.
"""

CYCLE = ("hero", "row", "row", "wide", "row")


def _feature(block):
    """Move the first item with a picture to the front. Returns False if none has one."""
    for index, item in enumerate(block):
        if item.thumbnail:
            block.insert(0, block.pop(index))
            return True
    return False


def layout(items, block_index=0, limit=30):
    """Lay items out in blocks, starting at position `block_index` of the cycle.

    Stops at the first block boundary once at least `limit` items are placed, so
    a page always ends on a complete block. Items are taken from the front of
    `items` in order, and each gets a `mag_slot` attribute: "hero", "wide" or "".

    Returns (placed_items, consumed, next_block_index). `consumed` counts how many
    of `items` were used (always a prefix), so the caller knows where the next
    page starts.
    """
    placed, consumed, b = [], 0, block_index
    while consumed < len(items) and len(placed) < limit:
        kind = CYCLE[b % len(CYCLE)]
        if kind == "hero":
            block = list(items[consumed:consumed + 3])
            slots = ["hero", "", ""] if len(block) == 3 and _feature(block) else [""] * len(block)
        elif kind == "wide":
            block = list(items[consumed:consumed + 2])
            if len(block) == 2 and _feature(block):
                slots = ["wide", ""]
            else:  # no picture: a plain row of three keeps the rows whole
                block = list(items[consumed:consumed + 3])
                slots = [""] * len(block)
        else:
            block = list(items[consumed:consumed + 3])
            slots = [""] * len(block)
        for item, slot in zip(block, slots):
            item.mag_slot = slot
            placed.append(item)
        consumed += len(block)
        b += 1
    return placed, consumed, b
