import base64
import hashlib
import io
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, ImageDraw
from pydantic import ValidationError

from app import main


class ExactPreviewTests(unittest.TestCase):
    def request(self, **overrides):
        data = {
            "profile": "nameplate7",
            "background": "#0B1220",
            "text": {
                "fio": {"value": "ИВАНОВ И.И.", "size": 121, "color": "#f3f5f7"},
                "title": {"value": "Руководитель отдела разработки", "size": 64, "color": "#e5e7eb"},
            },
        }
        data.update(overrides)
        return main.RenderRequest(**data)

    def background(self):
        image = Image.new("RGBA", (300, 800), (0, 60, 130, 255))
        ImageDraw.Draw(image).rectangle((80, 100, 220, 650), fill=(220, 40, 60, 160))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")

    def test_preview_jpeg_matches_gateway_payload_byte_for_byte(self):
        for profile in ("nameplate7", "nameplate10"):
            for styled in (False, True):
                for with_background in (False, True):
                    with self.subTest(profile=profile, styled=styled, background=with_background):
                        request = self.request(profile=profile)
                        request.text.fio.bold = styled
                        request.text.fio.italic = styled
                        request.text.fio.underline = styled
                        request.text.fio.position = "bottom-right" if styled else "center"
                        request.text.title.offset_y = 0 if styled else 18
                        if with_background:
                            request.background_image_base64 = self.background()
                        with patch.object(main, "gateway_post", return_value={"code": 0}) as gateway:
                            preview = main.render(request)
                            gateway.assert_not_called()
                            result = main.push(main.PushRequest(
                                mac="d48afcefab38", render=request, expected_jpg_sha256=preview.jpg_sha256,
                            ))
                        gateway.assert_called_once()
                        path, payload = gateway.call_args.args
                        self.assertEqual(path, "/device/updateDevImg")
                        self.assertEqual(payload["mac"], "d48afcefab38")
                        self.assertEqual(payload["image"], preview.jpg_base64)
                        jpeg = base64.b64decode(payload["image"])
                        self.assertEqual(hashlib.sha256(jpeg).hexdigest(), preview.jpg_sha256)
                        self.assertEqual(result.jpg_bytes, len(jpeg))
                        expected = main.SIZE_PROFILES[profile]
                        with Image.open(io.BytesIO(jpeg)) as decoded:
                            self.assertEqual(decoded.format, "JPEG")
                            self.assertEqual(decoded.size, (expected["w"], expected["h"]))
                            self.assertEqual(decoded.size, (preview.width, preview.height))

    def test_changed_settings_are_rejected_before_sending(self):
        request = self.request()
        preview = main.render(request)
        request.text.fio.bold = True
        with patch.object(main, "gateway_post") as gateway:
            with self.assertRaises(HTTPException) as error:
                main.push(main.PushRequest(mac="d48afcefab38", render=request, expected_jpg_sha256=preview.jpg_sha256))
            self.assertEqual(error.exception.status_code, 409)
            gateway.assert_not_called()

    def test_changed_renderer_output_is_rejected_before_sending(self):
        request = self.request()
        preview = main.render(request)
        # Covers changes to fonts/settings on the server after the operator saw the preview.
        with patch.object(main, "render_to_jpg", return_value=(b"different output", 1024, 600)):
            with patch.object(main, "gateway_post") as gateway:
                with self.assertRaises(HTTPException) as error:
                    main.push(main.PushRequest(mac="d48afcefab38", render=request, expected_jpg_sha256=preview.jpg_sha256))
                self.assertEqual(error.exception.status_code, 409)
                gateway.assert_not_called()

    def test_existing_api_clients_can_send_without_preview_hash(self):
        with patch.object(main, "gateway_post", return_value={"code": 0}) as gateway:
            main.push(main.PushRequest(mac="d48afcefab38", render=self.request()))
        gateway.assert_called_once()

    def test_invalid_preview_hash_is_rejected(self):
        for value in ("", "not-a-hash", "a" * 63):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                main.PushRequest(mac="d48afcefab38", render=self.request(), expected_jpg_sha256=value)

    def test_font_selection_changes_render_and_matches_sent_jpeg(self):
        families = main.available_fonts()
        self.assertGreaterEqual(len(families), 2, "Install the documented server fonts")
        output_hashes = set()
        for family in families:
            with self.subTest(family=family["id"]):
                request = self.request()
                request.text.fio.font_family = family["id"]
                request.text.fio.bold = True
                request.text.fio.italic = True
                request.text.title.font_family = family["id"]
                preview = main.render(request)
                output_hashes.add(preview.jpg_sha256)
                with patch.object(main, "gateway_post", return_value={"code": 0}) as gateway:
                    main.push(main.PushRequest(mac="d48afcefab38", render=request, expected_jpg_sha256=preview.jpg_sha256))
                self.assertEqual(gateway.call_args.args[1]["image"], preview.jpg_base64)
        self.assertGreater(len(output_hashes), 1)

    def test_fio_and_title_font_can_be_selected_independently(self):
        fonts = main.available_fonts()
        alternate = next(f["id"] for f in fonts if main.font_files(f["id"]) != main.font_files("default"))
        request = self.request()
        original = main.render(request).jpg_sha256
        request.text.title.font_family = alternate
        title_changed = main.render(request).jpg_sha256
        request.text.fio.font_family = alternate
        both_changed = main.render(request).jpg_sha256
        self.assertEqual(len({original, title_changed, both_changed}), 3)

    def test_unknown_or_missing_font_does_not_fall_back_silently(self):
        for family in ("unknown", "../../Windows/Fonts/arial.ttf", "missing"):
            with self.subTest(family=family), patch.dict(main.FONT_FAMILIES, {"missing": ("Missing", "/nonexistent", ("no.ttf",) * 4)}):
                request = self.request()
                request.text.fio.font_family = family
                with self.assertRaises(HTTPException) as error:
                    main.render(request)
                self.assertEqual(error.exception.status_code, 422)
                self.assertNotIn("missing", [f["id"] for f in main.available_fonts()])


if __name__ == "__main__":
    unittest.main()
