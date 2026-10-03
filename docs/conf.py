import re
import sys
from pathlib import Path

from docutils import nodes
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.environment import BuildEnvironment
from sphinx.ext.autodoc import Options

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dankmemer import __version__

project = "dankmemer.py"
author = "RayyanW786"
release = __version__
version = release
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "aiohttp": ("https://docs.aiohttp.org/en/stable/", None),
    "discord": ("https://discordpy.readthedocs.io/en/stable/", None),
    "asyncpg": ("https://magicstack.github.io/asyncpg/current/", None),
}
intersphinx_timeout = 10
nitpicky = True
# These TypeVars preserve the caller's return type; they have no public pages.
nitpick_ignore = [
    ("py:class", "dankmemer.client._Callback"),
    ("py:class", "dankmemer.ext.dpy.dank_cog._Callback"),
    ("py:class", "dankmemer._coordination_storage._T"),
    ("py:class", "dankmemer._sqlite_storage._Result"),
    ("py:class", "dankmemer._postgres_storage._Result"),
    # asqlite does not publish a Sphinx inventory for its connection type.
    ("py:class", "asqlite.Connection"),
]
autodoc_typehints = "signature"
autodoc_typehints_format = "short"
autodoc_member_order = "bysource"
autodoc_default_options = {"exclude-members": "__weakref__"}
napoleon_google_docstring = False
napoleon_numpy_docstring = True
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "style_nav_header_background": "#31513b",
}
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_title = f"dankmemer.py {release}"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
pygments_style = "sphinx"

_RESOURCE_TYPES = {
    "Items": ("Item", "int"),
    "Pets": ("Pet", "str"),
    "Skins": ("Skin", "str"),
    "Commands": ("Command", "str"),
    "Creatures": ("FishingCreature", "str"),
    "Locations": ("FishingLocation", "str"),
    "NPCs": ("FishingNPC", "str"),
    "Tools": ("FishingTool", "str"),
    "Baits": ("FishingBait", "str"),
    "Buckets": ("FishingBucket", "str"),
    "Skills": ("FishingSkill", "str"),
    "Blogs": ("Blog", "str"),
    "Changelogs": ("Changelog", "str"),
    "Drops": ("Drop", "int"),
    "StoreSales": ("StoreSale", "int"),
    "StoreDailyGifts": ("StoreDailyGift", "str"),
    "FishingEvents": ("FishingEvent", "str"),
}


def _resource_signature(
    app: Sphinx,
    what: str,
    name: str,
    obj: object,
    options: Options,
    signature: str | None,
    return_annotation: str | None,
) -> tuple[str | None, str | None] | None:
    # Autodoc sees the base method's TypeVars instead of the resource's models.
    parts = name.split(".")
    if what != "method" or len(parts) < 2:
        return None
    types = _RESOURCE_TYPES.get(parts[-2])
    if types is None:
        return None
    model, id_type = types

    def resolve(annotation: str | None) -> str | None:
        if annotation is None:
            return None
        annotation = re.sub(r"\b(?:\w+\.)*_T\b", f"dankmemer.{model}", annotation)
        return re.sub(r"\b(?:\w+\.)*_ID\b", id_type, annotation)

    return resolve(signature), resolve(return_annotation)


def _type_reference(
    app: Sphinx,
    env: BuildEnvironment,
    node: addnodes.pending_xref,
    content: nodes.Element,
) -> nodes.reference | None:
    # Short names in docstrings and recursive annotations need explicit targets.
    if node.get("refdomain") != "py":
        return None
    target = node["reftarget"]
    if target == "datetime":
        node["reftarget"] = "datetime.datetime"
    elif target == "aiohttp.client.ClientSession":
        node["reftarget"] = "aiohttp.ClientSession"
    elif target == "JSONValue":
        return env.get_domain("py").resolve_xref(
            env,
            node["refdoc"],
            app.builder,
            "type",
            "dankmemer.JSONValue",
            node,
            content,
        )
    return None


def setup(app: Sphinx) -> None:
    app.connect("autodoc-process-signature", _resource_signature)
    app.connect("missing-reference", _type_reference, priority=400)
