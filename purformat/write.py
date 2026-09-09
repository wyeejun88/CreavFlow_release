import hashlib
import struct

from .items import PurGraphicsImageItem, PurGraphicsTextItem
from .purformat import PurFile


def write_pur_file(pur_file: PurFile, filepath: str):
    pur_bytes = bytearray()

    def pack_add(typ: str, *args):
        pur_bytes.extend(struct.pack(typ, *args))

    def pack_add_matrix(matrix):
        pack_add(">d", matrix[0])
        pack_add(">d", matrix[1])
        pack_add(">d", 0.0)
        pack_add(">d", matrix[2])
        pack_add(">d", matrix[3])
        pack_add(">d", 0.0)

    def pack_add_rgb(rgb):
        for value in rgb:
            pur_bytes.extend(struct.pack(">H", value))

    def pack_add_string(string: str):
        encoded = string.encode("utf-16-be")
        pack_add(">I", len(encoded))
        pur_bytes.extend(encoded)

    def write_header():
        pur_bytes[:] = bytearray(b"\x00") * 224
        pur_bytes[0:4] = struct.pack(">I", 8)
        pur_bytes[4:12] = "1.10".encode("utf-16-be")
        image_items = pur_file.count_image_items()
        text_items = pur_file.count_text_items(image_items)
        pur_bytes[12:14] = struct.pack(">H", image_items + text_items)
        pur_bytes[14:16] = struct.pack(">H", image_items)
        pur_bytes[24:28] = struct.pack(">I", 12)
        pur_bytes[40:44] = struct.pack(">I", 64)
        pur_bytes[108:112] = struct.pack(">I", image_items + text_items)
        pur_bytes[112:144] = (
            struct.pack(">d", pur_file.canvas[0])
            + struct.pack(">d", pur_file.canvas[1])
            + struct.pack(">d", pur_file.canvas[2])
            + struct.pack(">d", pur_file.canvas[3])
        )
        pur_bytes[144:152] = struct.pack(">d", pur_file.zoom)
        pur_bytes[176:184] = struct.pack(">d", pur_file.zoom)
        pur_bytes[208:216] = struct.pack(">d", 1.0)
        pur_bytes[216:224] = struct.pack(">i", pur_file.xCanvas) + struct.pack(
            ">i", pur_file.yCanvas
        )

    def write_images():
        for image_add in pur_file.images:
            image_add.address[0] = len(pur_bytes)
            pur_bytes.extend(image_add.pngBinary)
            image_add.address[1] = len(pur_bytes)
            parent = image_add.transforms[0]
            for _ in image_add.transforms[1:]:
                pack_add(">I", parent.id)

    def write_text(text_transform: PurGraphicsTextItem):
        transform_end = len(pur_bytes)
        pur_bytes.extend(struct.pack(">Q", 0))
        pack_add(">I", 32)
        pur_bytes.extend("GraphicsTextItem".encode("utf-16-be"))
        pack_add_string(text_transform.text)
        pack_add_matrix(text_transform.matrix)
        pack_add(">d", text_transform.x)
        pack_add(">d", text_transform.y)
        pack_add(">d", 1.0)
        pack_add(">I", text_transform.id)
        pack_add(">d", text_transform.zLayer)
        pack_add(">b", 1)
        pack_add(">H", text_transform.opacity)
        pack_add_rgb(text_transform.rgb)
        pack_add(">H", 0)
        pack_add(">b", 1)
        pack_add(">H", text_transform.opacityBackground)
        pack_add_rgb(text_transform.rgbBackground)
        pack_add(">H", 0)
        pack_add(">I", len(text_transform.textChildren))
        pur_bytes[transform_end : transform_end + 8] = struct.pack(">Q", len(pur_bytes))
        for child in text_transform.textChildren:
            write_text(child)

    def write_image(transform: PurGraphicsImageItem):
        transform_end = len(pur_bytes)
        pack_add(">Q", 0)
        brute_force_loaded = transform.source == "BruteForceLoaded"
        pack_add(">I", 34)
        pur_bytes.extend("GraphicsImageItem".encode("utf-16-be"))
        if brute_force_loaded:
            pack_add(">I", 0)
        pack_add_string(transform.source)
        if not brute_force_loaded:
            pack_add_string(transform.name)
        pack_add(">d", 1.0)
        pack_add_matrix(transform.matrix)
        pack_add(">d", transform.x)
        pack_add(">d", transform.y)
        pack_add(">d", 1.0)
        pack_add(">I", transform.id)
        pack_add(">d", transform.zLayer)
        pack_add_matrix(transform.matrixBeforeCrop)
        pack_add(">d", transform.xCrop)
        pack_add(">d", transform.yCrop)
        pack_add(">d", transform.scaleCrop)
        pack_add(">I", len(transform.points[0]))
        for i in range(len(transform.points[0])):
            pack_add(">I", 0 if i == 0 else 1)
            pack_add(">d", transform.points[0][i])
            pack_add(">d", transform.points[1][i])
        pack_add(">d", 0.0)
        pack_add(">I", 1)
        pack_add(">b", 0)
        pack_add(">q", -1)
        pack_add(">I", len(transform.textChildren))
        pur_bytes[transform_end : transform_end + 8] = struct.pack(">Q", len(pur_bytes))
        for child in transform.textChildren:
            write_text(child)

    def write_items():
        if pur_file.images:
            transforms = [
                transform for image in pur_file.images for transform in image.transforms
            ]
            for transform in transforms:
                write_image(transform)
        for text_transform in pur_file.text:
            write_text(text_transform)

    def write_references():
        for image in pur_file.images:
            for i, transform in enumerate(image.transforms):
                if i == 0:
                    pack_add(">I", transform.id)
                    pack_add(">Q", image.address[0])
                    pack_add(">Q", image.address[1])
                else:
                    offset = (i - 1) * 4
                    pack_add(">I", transform.id)
                    pack_add(">Q", image.address[1] + offset)
                    pack_add(">Q", image.address[1] + offset + 4)

    write_header()
    write_images()
    write_items()
    pack_add_string(pur_file.folderLocation)
    pur_bytes[16:24] = struct.pack(">Q", len(pur_bytes))
    write_references()
    pur_bytes[44:108] = hashlib.md5(pur_bytes[108:]).hexdigest().encode("utf-16-be")
    with open(filepath, "wb") as handle:
        handle.write(pur_bytes)
