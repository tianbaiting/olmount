import pytest
from olmount.sync.ot import diff_ops, apply_ops

def test_pure_insert_at_start():
    assert diff_ops("xyz", "axyz") == [{"p": 0, "i": "a"}]

def test_pure_delete():
    assert diff_ops("abc", "ac") == [{"p": 1, "d": "b"}]

def test_bmp_offsets():
    # euro sign U+20AC is BMP (1 UTF-16 unit)
    ops = diff_ops("a€c", "aX€c")
    assert ops == [{"p": 1, "i": "X"}]   # position after "a" == 1

def test_astral_offset_counts_two_units():
    # U+1F600 grinning face -> 2 UTF-16 units
    ops = diff_ops("😀b", "X😀b")
    assert ops == [{"p": 0, "i": "X"}]
    ops2 = diff_ops("a😀", "a")            # delete the astral char at offset 1 (length 2)
    assert ops2 == [{"p": 1, "d": "😀"}]

def test_mixed_string_insert_position():
    ops = diff_ops("ab😀ef", "ab😀Zef")
    assert ops == [{"p": 4, "i": "Z"}]     # a,b,=2 ; 😀=2 -> offset 4

@pytest.mark.parametrize("a,b", [
    ("", ""), ("abc","abc"), ("abc","abxyc"), ("Hello world","Hello cruel world"),
    ("line1\nline2\nline3","line1\nline2 CHANGED\nline3 extra"),
    ("😀😀😀","😀😂😀"),                    # astral substitutions
    ("café résumé","café résumé naïve"),   # BMP accents
    ("a"*1000, "a"*500+"b"+"a"*500),
])
def test_property_apply_reconstructs_target(a, b):
    assert apply_ops(diff_ops(a, b), a) == b

def test_empty_ops_when_equal():
    assert diff_ops("same", "same") == []
