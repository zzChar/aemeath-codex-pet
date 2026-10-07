"""Deterministically crop generated RGBA strips; never redraw the character."""
from pathlib import Path
from PIL import Image

root=Path(__file__).resolve().parents[1]
for kind in ("sleep","celebrate","workbench","thinking"):
    source=Image.open(root/f"assets/{kind}-source.png").convert("RGBA")
    cells=[source.crop((i*source.width//6,0,(i+1)*source.width//6,source.height)) for i in range(6)]
    boxes=[cell.getchannel("A").point(lambda a:255 if a>128 else 0).getbbox() for cell in cells]
    top=min(b[1] for b in boxes);bottom=max(b[3] for b in boxes)
    width=max(b[2]-b[0] for b in boxes)
    scale=min(180/width,({"celebrate":196,"thinking":196,"workbench":180}.get(kind,174))/(bottom-top))
    result=Image.new("RGBA",(1152,208))
    for i,(cell,box) in enumerate(zip(cells,boxes)):
        # Shared vertical range preserves the jump instead of aligning each
        # frame's feet independently and erasing the motion.
        crop=cell.crop((box[0],top,box[2],bottom))
        crop=crop.resize((round(crop.width*scale),round(crop.height*scale)),Image.Resampling.NEAREST)
        result.alpha_composite(crop,(i*192+(192-crop.width)//2,200-crop.height))
    result.save(root/f"assets/{kind}.png")
    print(kind,source.size,boxes)
