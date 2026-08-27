from __future__ import annotations

import unittest

from fastapi import HTTPException

from esg_encoding.services import company_report_service, report_service


class _UnreadablePdf:
    filename = "report.pdf"

    async def read(self) -> bytes:
        raise AssertionError("A retired framework must be rejected before reading the PDF")


class RetiredFrameworkUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_report_tcfd_upload_is_rejected_before_file_read(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            await report_service.upload_report(
                file=_UnreadablePdf(),
                industry="TCFD",
                semiIndustry="governance",
                framework=" tcfd ",
                griSector=None,
                griTopic=None,
                scopeSlugs=None,
                user_id=1,
            )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("no longer available", str(caught.exception.detail))

    async def test_company_batch_tcfd_upload_is_rejected_before_file_read(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            await company_report_service.upload_report_batch(
                files=[_UnreadablePdf()],
                uploadMode="single",
                companyId=None,
                companyName="Example",
                reportYears=None,
                industry="TCFD",
                semiIndustry="governance",
                framework="TCFD",
                griSector=None,
                griTopic=None,
                scopeSlugs=None,
                user_id=1,
            )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("no longer available", str(caught.exception.detail))


if __name__ == "__main__":
    unittest.main()
