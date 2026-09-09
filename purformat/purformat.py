from typing import List
import os

from .items import PurImage, PurGraphicsTextItem


class PurFile:
    def __init__(self):
        self.canvas = [-10000.0, -10000.0, 10000.0, 10000.0]
        self.zoom = 1.0
        self.xCanvas, self.yCanvas = 0, 0
        self.folderLocation = os.getcwd()
        self.images: List[PurImage] = []
        self.text: List[PurGraphicsTextItem] = []

    def write(self, file: str):
        from .write import write_pur_file

        write_pur_file(self, file)

    def count_image_items(self):
        count = 0
        for image in self.images:
            for transform in image.transforms:
                transform.id = count
                count += 1
        return count

    def count_text_items(self, id_offset: int):
        count = 0

        def count_children(text_item: PurGraphicsTextItem):
            nonlocal count
            text_item.id = count + id_offset
            count += 1
            for child in text_item.textChildren:
                count_children(child)

        for item in self.text:
            count_children(item)
        return len(self.text)
