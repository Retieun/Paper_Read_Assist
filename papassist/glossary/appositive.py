"""'the vertex $i$', 'a degree $q$': the noun phrase right before a symbol names it."""
from __future__ import annotations

import re
from typing import Optional

from .textutil import PH_RE

ARTICLES = {"the", "a", "an", "its", "each", "every", "any", "some", "this", "that", "these", "those", "our", "their", "another", "one"}
STOP_WORDS = {"of", "in", "on", "by", "to", "for", "with", "at", "from", "and", "or", "as", "than", "that", "which", "where",
              "is", "are", "be", "let", "fix", "if", "then", "so", "all", "both", "either", "neither", "no", "not", "denoted",
              "written", "called", "defined", "given", "following", "corresponding", "same", "other", "above", "below",
              "previous", "next", "former", "latter", "usual", "standard", "resulting", "associated", "induced", "natural",
              "canonical", "obvious", "whose", "when", "while", "but", "we", "it", "they", "he", "she", "you", "there", "here",
              "via", "under", "over", "into", "onto", "through", "between", "among", "along", "after", "before", "since",
              "using", "use", "consider", "take", "put", "set", "write", "define", "obtain", "get", "have", "has", "being",
              "such", "case", "value", "condition", "assumption", "hypothesis", "fact", "sense", "way", "hand", "side",
              "first", "second", "third", "last", "new", "old", "only", "even", "also", "still", "now", "then", "thus"}
HEAD_NOUNS = {"map", "maps", "element", "elements", "number", "numbers", "vertex", "vertices", "degree", "degrees", "face", "faces",
              "ideal", "ideals", "ring", "rings", "module", "modules", "complex", "complexes", "field", "fields", "space", "spaces",
              "functor", "functors", "filtration", "filtrations", "parameter", "parameters", "prime", "primes", "set", "sets",
              "sequence", "sequences", "function", "functions", "morphism", "morphisms", "point", "points", "index", "indices",
              "integer", "integers", "constant", "constants", "variable", "variables", "matrix", "matrices", "graph", "graphs",
              "subcomplex", "subcomplexes", "category", "categories", "group", "groups", "homomorphism", "homomorphisms",
              "isomorphism", "isomorphisms", "object", "objects", "subset", "subsets", "multidegree", "multidegrees", "barcode",
              "barcodes", "interval", "intervals", "poset", "posets", "diagram", "diagrams", "resolution", "resolutions", "distance",
              "distances", "metric", "metrics", "operator", "operators", "transformation", "transformations", "cover", "covers",
              "simplex", "simplices", "cone", "cones", "monomial", "monomials", "polynomial", "polynomials", "grading", "gradings",
              "basis", "bases", "coefficient", "coefficients", "quotient", "quotients", "surjection", "surjections", "injection",
              "injections", "inclusion", "inclusions", "subspace", "subspaces", "submodule", "submodules", "algebra", "algebras",
              "scheme", "schemes", "variety", "varieties", "manifold", "manifolds", "bundle", "bundles", "sheaf", "sheaves",
              "measure", "measures", "vector", "vectors", "form", "forms", "structure", "structures", "invariant", "invariants",
              "dimension", "dimensions", "family", "families", "collection", "collections", "pair", "pairs", "tuple", "tuples",
              "path", "paths", "cycle", "cycles", "chain", "chains", "boundary", "boundaries", "component", "components", "summand",
              "summands", "factor", "factors", "term", "terms", "shift", "shifts", "weight", "weights", "threshold", "thresholds",
              "level", "levels", "scale", "scales", "time", "times", "step", "steps", "position", "positions", "direction",
              "directions", "homology", "cohomology", "torsion", "support", "rank", "length", "height", "depth", "radius", "diameter",
              "norm", "order", "type", "types", "line", "lines", "plane", "planes", "curve", "curves", "surface", "surfaces",
              "sphere", "spheres", "ball", "balls", "disk", "disks", "torus", "tori", "knot", "knots", "link", "links", "star",
              "stars", "deletion", "deletions", "localization", "localizations", "extension", "extensions", "restriction",
              "restrictions", "projection", "projections", "embedding", "embeddings", "action", "actions", "generator",
              "generators", "relation", "relations", "differential", "differentials", "kernel", "kernels", "image", "images",
              "cokernel", "cokernels", "limit", "limits", "colimit", "colimits", "product", "products", "coproduct", "coproducts",
              "sum", "sums", "series", "integral", "integrals", "derivative", "derivatives", "solution", "solutions", "root",
              "roots", "eigenvalue", "eigenvalues", "eigenvector", "eigenvectors", "operator", "matrix", "tensor", "tensors",
              "bar", "bars", "birth", "death", "endpoint", "endpoints", "witness", "witnesses", "representative", "representatives",
              "class", "classes", "cell", "cells", "block", "blocks", "row", "rows", "column", "columns", "entry", "entries",
              "coordinate", "coordinates", "axis", "axes", "region", "regions", "domain", "domains", "codomain", "range", "target",
              "source", "fiber", "fibers", "fibre", "fibres", "section", "sections", "germ", "germs", "stalk", "stalks", "atom",
              "atoms", "residue", "residues", "molecule", "molecules", "protein", "proteins", "sample", "samples", "data", "dataset",
              "datasets", "ideal", "prime", "power", "powers", "exponent", "exponents", "multiplicity", "multiplicities",
              "characteristic", "signature", "signatures", "trace", "traces", "determinant", "determinants", "polynomial", "seed",
              "seeds", "stratum", "strata", "stratification", "stratifications", "neighbourhood", "neighborhood", "neighborhoods",
              "neighbourhoods", "open", "closure", "interior", "boundary", "frontier", "hull", "hulls", "envelope", "envelopes",
              "lattice", "lattices", "semigroup", "semigroups", "monoid", "monoids", "field", "extension", "tower", "towers",
              "degree", "genus", "grade", "grades", "piece", "pieces", "part", "parts", "portion", "portions", "slice", "slices",
              "layer", "layers", "copy", "copies", "version", "versions", "analogue", "analog", "analogues", "analogs", "variant",
              "variants", "case", "instance", "instances", "example", "examples", "counterexample", "counterexamples"}


def appositive_candidates(sent: str) -> list[tuple[str, str]]:
    """Return (mid, noun phrase) pairs for symbols preceded by a short noun phrase.

    The phrase is the run of plain words between the last article and the
    symbol, at most three words, with a recognised head noun at the end.
    """
    out: list[tuple[str, str]] = []
    for m in PH_RE.finditer(sent):
        mid = m.group(1)
        # must be followed by punctuation / space / end, not by '-word' or another symbol
        after = sent[m.end(): m.end() + 1]
        if after and after not in " .,;:)]} \n":
            continue
        before = sent[: m.start()].rstrip()
        if not before or before[-1] in ".,;:(⟧":
            continue
        # walk backwards over plain words
        toks = re.findall(r"[A-Za-z][A-Za-z\-']*|\S", before)
        words: list[str] = []
        ok = False
        for tok in reversed(toks):
            low = tok.lower()
            if not re.fullmatch(r"[a-z][a-z\-']*", low) or tok[0].isupper() and len(words) > 0:
                break
            if low in ARTICLES:
                ok = True
                break
            if low in STOP_WORDS:
                break
            if tok[0].isupper():
                break
            words.insert(0, tok)
            if len(words) > 3:
                break
        if not ok or not words or len(words) > 3:
            continue
        head = words[-1].lower()
        if head not in HEAD_NOUNS:
            continue
        out.append((mid, " ".join(words)))
    return out
