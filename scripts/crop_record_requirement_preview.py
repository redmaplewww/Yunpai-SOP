from pathlib import Path

from PIL import Image


SOURCE = Path(
    r"D:\ui长期修改\Yunpai-SOP\outputs\deliverables\hdmi_process_knowledge_sop_20260812"
    r"\knowledge\generated_documents\route_1\preview\page-004.png"
)
OUTPUT = SOURCE.with_name("page-004-record-requirement-zoom.png")


def main() -> None:
    with Image.open(SOURCE) as image:
        # The right-side record section occupies the middle-right of this landscape SOP page.
        crop = image.crop((1045, 390, 1404, 700))
        crop.resize((1436, 1240), Image.Resampling.LANCZOS).save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
