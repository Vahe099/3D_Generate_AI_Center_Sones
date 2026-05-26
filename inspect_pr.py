import rhino3dm, sys
model = rhino3dm.File3dm.Read(r"3dm\GS_62\GS_62_PR.3dm")
if not model:
    print("ERROR: cannot read file")
    sys.exit(1)

print(f"Total objects: {len(model.Objects)}")
print()

for i, obj in enumerate(model.Objects):
    g = obj.Geometry
    a = obj.Attributes
    layer_idx = a.LayerIndex
    layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else "?"
    visible = a.Visible
    name = a.Name or ""
    gtype = type(g).__name__

    bb = g.GetBoundingBox()
    if bb:
        sx = round(bb.Max.X - bb.Min.X, 2)
        sy = round(bb.Max.Y - bb.Min.Y, 2)
        sz = round(bb.Max.Z - bb.Min.Z, 2)
        cx = round((bb.Max.X + bb.Min.X) / 2, 3)
        cy = round((bb.Max.Y + bb.Min.Y) / 2, 3)
        cz = round((bb.Max.Z + bb.Min.Z) / 2, 3)
        footprint = round(max(sx, sy), 2)
    else:
        sx=sy=sz=cx=cy=cz=footprint=0

    flag = " *** LARGE" if footprint > 20 else ""
    flag += " [HIDDEN]" if not visible else ""
    flag += " [GEM]" if "gem" in layer_name.lower() else ""
    print(f"  [{i:2d}] layer={layer_name:<20} type={gtype:<20} name={name:<30} cz={cz:>8.3f} sz={sz:>7.3f} fp={footprint:>7.2f}{flag}")
