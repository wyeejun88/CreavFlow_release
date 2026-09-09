from typing import List


class Item:
    def __init__(self):
        self.id = 0
        self.zLayer = 1.0
        self.matrix = [1.0, 0.0, 0.0, 1.0]
        self.x, self.y = 0.0, 0.0
        self.textChildren: List["PurGraphicsTextItem"] = []


class PurGraphicsTextItem(Item):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.opacity = 65535
        self.rgb = [65535, 65535, 65535]
        self.opacityBackground = 5000
        self.rgbBackground = [0, 0, 0]


class PurGraphicsImageItem(Item):
    def __init__(self):
        super().__init__()
        self.source = "BruteForceLoaded"
        self.name = "image"
        self.matrixBeforeCrop = [1.0, 0.0, 0.0, 1.0]
        self.xCrop, self.yCrop = 0.0, 0.0
        self.scaleCrop = 1.0
        self.points = [
            [-1000, 1000, 1000, -1000, -1000],
            [-1000, -1000, 1000, 1000, -1000],
        ]

    @property
    def width(self):
        return (self.points[0][2] - self.points[0][0]) * self.matrix[0]

    @width.setter
    def width(self, value):
        self.matrix[0] = value / (self.points[0][2] - self.points[0][0])

    @property
    def height(self):
        return (self.points[1][2] - self.points[1][0]) * self.matrix[3]

    @height.setter
    def height(self, value):
        self.matrix[3] = value / (self.points[1][2] - self.points[1][0])

    def scale(self, factor):
        self.matrix[0] *= factor
        self.matrix[3] *= factor

    def reset_crop(self, width, height):
        w = width / 2
        h = height / 2
        self.xCrop, self.yCrop = -float(w), -float(h)
        self.points = [
            [-w, w, w, -w, -w],
            [-h, -h, h, h, -h],
        ]


class PurImage:
    def __init__(self):
        self.address = [0, 0]
        self.pngBinary = bytearray()
        self.transforms: List[PurGraphicsImageItem] = []
