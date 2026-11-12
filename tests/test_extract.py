import io

from client_inbox import extract


def test_text_and_html():
    assert extract.extract_text("notes.txt", b"hello world") == "hello world"
    assert extract.extract_text("page.html", b"<style>x{}</style><p>Hi <b>there</b></p>") == "Hi there"


def test_docx():
    import docx

    document = docx.Document()
    document.add_paragraph("Rate card: $150 per hour")
    buffer = io.BytesIO()
    document.save(buffer)
    assert "Rate card: $150 per hour" in extract.extract_text("rates.docx", buffer.getvalue())


def test_xlsx():
    import openpyxl

    book = openpyxl.Workbook()
    book.active.append(["Item", "Price"])
    book.active.append(["Audit", 1200])
    buffer = io.BytesIO()
    book.save(buffer)
    text = extract.extract_text("prices.xlsx", buffer.getvalue())
    assert "Audit | 1200" in text


def test_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Invoice total 4200) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert "Invoice total 4200" in extract.extract_text("invoice.pdf", buffer.getvalue())


def test_unreadable_types_return_empty():
    assert extract.extract_text("photo.png", b"\x89PNG....") == ""
    assert extract.extract_text("broken.pdf", b"not a pdf") == ""


def test_chunks_cover_all_text():
    text = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(10))
    pieces = extract.chunks(text, size=1000)
    assert all(len(piece) <= 1000 for piece in pieces)
    assert sum(piece.count("paragraph") for piece in pieces) == 10
