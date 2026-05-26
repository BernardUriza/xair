# conda recipe for xair

Build locally:

```bash
conda install -n base -c conda-forge conda-build
conda build conda-recipe/ -c conda-forge
```

Install the built package:

```bash
conda install -c local xair
```

Or in a fresh env:

```bash
conda create -n xair-env -c local -c conda-forge xair python=3.12
```

For tagged releases, edit `source:` in `meta.yaml` to pull a GitHub tag
tarball with `sha256` instead of `path: ..`. Then submit to conda-forge
(`staged-recipes`) for distribution beyond `-c local`.
