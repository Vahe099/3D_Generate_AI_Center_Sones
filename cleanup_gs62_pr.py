"""Remove Gem-layer reference stones from GS_62_PR.3dm (indices 0-6)."""
import rhino3dm, sys

FILE = r"3dm\GS_62\GS_62_PR.3dm"
model = rhino3dm.File3dm.Read(FILE)
if not model:
    print("ERROR: cannot read file")
    sys.exit(1)

print(f"Before: {len(model.Objects)} objects")

# Collect GUIDs of Gem-layer reference stones to remove
to_remove = []
for i, obj in enumerate(model.Objects):
    layer_idx = obj.Attributes.LayerIndex
    layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else ""
    if layer_name.lower() == "gem":
        name = obj.Attributes.Name or ""
        bb = obj.Geometry.GetBoundingBox()
        cz = round((bb.Max.Z + bb.Min.Z) / 2, 3) if bb else 0
        print(f"  MARK REMOVE [{i:2d}] layer={layer_name} name={name!r} cz={cz}")
        to_remove.append(obj.Attributes.Id)

print(f"\nRemoving {len(to_remove)} Gem-layer reference stones ...")
for guid in to_remove:
    result = model.Objects.Delete(guid)
    print(f"  Delete {guid} -> {result}")

print(f"\nAfter: {len(model.Objects)} objects")

# Verify remaining objects
print("\nRemaining objects:")
for i, obj in enumerate(model.Objects):
    layer_idx = obj.Attributes.LayerIndex
    layer_name = model.Layers[layer_idx].Name if layer_idx < len(model.Layers) else "?"
    gtype = type(obj.Geometry).__name__
    name = obj.Attributes.Name or ""
    bb = obj.Geometry.GetBoundingBox()
    if bb:
        cz = round((bb.Max.Z + bb.Min.Z) / 2, 3)
        sz = round(bb.Max.Z - bb.Min.Z, 3)
        fp = round(max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y), 2)
    else:
        cz = sz = fp = 0
    print(f"  [{i:2d}] layer={layer_name:<20} type={gtype:<20} name={name:<25} cz={cz:>8.3f} sz={sz:>7.3f} fp={fp:>7.2f}")

model.Write(FILE, 7)
print(f"\nWritten: {FILE}")
