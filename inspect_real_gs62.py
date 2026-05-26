"""Quick inspection of real GS_62 MQ/OV/RD: look for Gem-layer objects and stone positions."""
import rhino3dm
from pathlib import Path

FILES = {
    "GS_62_MQ (real)": Path(r"3dm\GS_62\GS_62_MQ.3dm"),
    "GS_62_OV (real)": Path(r"3dm\GS_62\GS_62_OV.3dm"),
    "GS_62_RD (real)": Path(r"3dm\GS_62\GS_62_RD.3dm"),
}

for label, fpath in FILES.items():
    model = rhino3dm.File3dm.Read(str(fpath))
    if not model:
        print(f"{label}: ERROR")
        continue
    print(f"\n{'='*60}")
    print(f"  {label}  total={len(model.Objects)}")
    print(f"{'='*60}")
    print(f"  {'idx':>3}  {'layer':<22} {'type':<20} {'name':<28} {'cz':>8} {'sz':>7} {'fp':>7}")
    print(f"  {'---':>3}  {'-'*22} {'-'*20} {'-'*28} {'------':>8} {'------':>7} {'------':>7}")
    for i, obj in enumerate(model.Objects):
        g = obj.Geometry
        a = obj.Attributes
        layer_idx = a.LayerIndex
        layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else "?"
        name = a.Name or ""
        gtype = type(g).__name__
        bb = g.GetBoundingBox()
        if bb:
            sz = round(bb.Max.Z - bb.Min.Z, 2)
            cz = round((bb.Max.Z + bb.Min.Z) / 2, 3)
            fp = round(max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y), 2)
        else:
            sz = cz = fp = 0
        flag = " <-- GEM REF" if "gem" in layer_name.lower() and "gem 0" not in layer_name.lower() else ""
        flag += " *** LARGE" if fp > 100 else ""
        flag += " [sub-plane]" if cz < 2 else ""
        print(f"  {i:>3}  {layer_name:<22} {gtype:<20} {name:<28} {cz:>8.3f} {sz:>7.3f} {fp:>7.2f}{flag}")
