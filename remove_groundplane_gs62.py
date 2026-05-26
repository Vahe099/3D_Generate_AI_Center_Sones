"""Remove GroundPlane objects from GS_62_AS, GS_62_CU, GS_62_EM.
A GroundPlane is identified by footprint > 200mm (the 300x300 rendering plane).
"""
import rhino3dm, sys
from pathlib import Path

FILES = [
    r"3dm\GS_62\GS_62_AS.3dm",
    r"3dm\GS_62\GS_62_CU.3dm",
    r"3dm\GS_62\GS_62_EM.3dm",
]

for fpath in FILES:
    model = rhino3dm.File3dm.Read(fpath)
    if not model:
        print(f"ERROR: cannot read {fpath}")
        continue

    before = len(model.Objects)
    to_remove = []
    for i, obj in enumerate(model.Objects):
        g = obj.Geometry
        bb = g.GetBoundingBox()
        if not bb:
            continue
        sx = bb.Max.X - bb.Min.X
        sy = bb.Max.Y - bb.Min.Y
        footprint = max(sx, sy)
        layer_idx = obj.Attributes.LayerIndex
        layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else "?"
        cz = round((bb.Max.Z + bb.Min.Z) / 2, 3)

        if footprint > 200:
            print(f"  [{fpath}] [{i:2d}] MARK REMOVE  layer={layer_name}  fp={round(footprint,1)}  cz={cz}")
            to_remove.append(obj.Attributes.Id)

    if not to_remove:
        print(f"  [{fpath}] No GroundPlane found — skipping")
        continue

    for guid in to_remove:
        model.Objects.Delete(guid)

    after = len(model.Objects)
    model.Write(fpath, 7)
    print(f"  [{fpath}] {before} -> {after} objects  (removed {len(to_remove)})")
