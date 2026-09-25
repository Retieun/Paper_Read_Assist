from papassist.ingest.bib import BibEntry
from papassist.ingest.citations import alpha_label, compute_labels, label_mode


def entry(key, authors, year, title="T"):
    return BibEntry(key=key, entry_type="article", title=title, authors=authors, year=year)


BIB = {
    "zed": entry("zed", ["A. Zed"], "2001"),
    "adams": entry("adams", ["J. Adams", "B. Baker"], "1999"),
    "five": entry("five", ["A. One", "B. Two", "C. Three", "D. Four", "E. Five"], "2020"),
}


def test_label_modes():
    assert label_mode("plain", None) == "sorted"
    assert label_mode("unsrt", None) == "unsorted"
    assert label_mode("alpha", None) == "alpha"
    assert label_mode("amsalpha", None) == "alpha"
    assert label_mode("plainnat", None) == "authoryear"
    assert label_mode("plain", "alphabetic") == "alpha"
    assert label_mode(None, None) == "sorted"


def test_sorted_numeric_labels():
    labels, order = compute_labels(["zed", "adams", "zed"], BIB, "sorted")
    assert order == ["adams", "zed"] and labels == {"adams": "1", "zed": "2"}


def test_unsorted_labels_follow_first_citation():
    labels, order = compute_labels(["zed", "adams"], BIB, "unsorted")
    assert order == ["zed", "adams"] and labels["zed"] == "1"


def test_alpha_labels():
    assert alpha_label(BIB["zed"]) == "Zed01"
    assert alpha_label(BIB["adams"]) == "AB99"
    assert alpha_label(BIB["five"]) == "OTT+20"
    labels, _ = compute_labels(["zed", "adams", "five"], BIB, "alpha")
    assert labels == {"zed": "Zed01", "adams": "AB99", "five": "OTT+20"}


def test_missing_entries_and_no_bib():
    labels, order = compute_labels(["b", "a"], {}, "sorted")
    assert labels == {"b": "1", "a": "2"}                      # no bibliography: order of citation
    labels, order = compute_labels(["ghost", "zed"], BIB, "sorted")
    assert order == ["zed", "ghost"] and labels["ghost"] == "2"  # unknown keys go last


def test_authoryear_labels():
    labels, _ = compute_labels(["adams"], BIB, "authoryear")
    assert labels["adams"] == "Adams–Baker 1999"
