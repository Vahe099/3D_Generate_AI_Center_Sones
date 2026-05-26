"""
Clean real GS_62 MQ/OV/RD files:
  - Remove Gem-layer reference stones (layer name == "Gem" exactly)
  - Remove GroundPlane (footprint > 200mm)
Save cleaned copies as GS_62_<SHAPE>_clean.3dm. Originals untouched.
"""
import rhino3dm
from pathlib import Path

FILES = {
    "MQ": Path(r"3dm\GS_62\GS_62_MQ.3dm"),
    "OV": Path(r"3dm\GS_62\GS_62_OV.3dm"),
    "RD": Path(r"3dm\GS_62\GS_62_RD.3dm"),
}

for shape, src in FILES.items():
    dst = src.parent / f"GS_62_{shape}_clean.3dm"
    model = rhino3dm.File3dm.Read(str(src))
    if not model:
        print(f"{shape}: ERROR reading {src}")
        continue

    before = len(model.Objects)
    to_remove = []

    for obj in model.Objects:
        g = obj.Geometry
        a = obj.Attributes
        layer_idx = a.LayerIndex
        layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else ""

        # Remove: exactly "Gem" layer (reference stones, not "Gem 01"/"Gem 02"/"Gem 03")
        if layer_name.strip() == "Gem":
            to_remove.append((obj.Attributes.Id, f"Gem-ref layer='{layer_name}' name='{a.Name}'"))
            continue

        # Remove: GroundPlane (footprint > 200mm, any layer)
        bb = g.GetBoundingBox()
        if bb:
            fp = max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y)
            if fp > 200:
                to_remove.append((obj.Attributes.Id, f"GroundPlane layer='{layer_name}' fp={fp:.0f}mm"))

    print(f"\n{shape}: {src.name} -> {dst.name}")
    print(f"  Before : {before} objects")
    print(f"  Removing {len(to_remove)} objects:")
    for guid, reason in to_remove:
        model.Objects.Delete(guid)
        print(f"    - {reason}")
    print(f"  After  : {len(model.Objects)} objects")

    # Verify remaining: show summary
    print("  Remaining objects:")
    for i, obj in enumerate(model.Objects):
        g = obj.Geometry
        a = obj.Attributes
        layer_idx = a.LayerIndex
        layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else "?"
        gtype = type(g).__name__
        bb = g.GetBoundingBox()
        if bb:
            cz  = round((bb.Max.Z + bb.Min.Z) / 2, 3)
            fp  = round(max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y), 2)
        else:
            cz = fp = 0
        print(f"    [{i:2d}] {layer_name:<20} {gtype:<18} cz={cz:>8.3f}  fp={fp:>7.2f}")

    model.Write(str(dst), 7)
    print(f"  Written: {dst}")
