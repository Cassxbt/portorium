import re

from portorium.leakage import answer_fragments, extract_facts, strip_codes

NY_J83754 = (
    "NY J83754May 15, 2003CLA-2-65:RR:NC:3:353 J83754  CATEGORY:  ClassificationTARIFF NO.: 6505.90.2060  "
    "Mr. Stephen WongOceanland Service Inc.P.O. Box 12498054 E. Garvey Ave., #200Rosemead, CA 91770RE:\t"
    "The tariff classification of cap crown from China.Dear Mr. Wong:\tIn your letter dated April 18, 2003, on behalf "
    "of Golf Apparel Brands, Inc., you requested a tariff classification ruling.\tThe submitted sample is a cap crown "
    "constructed of woven 100% cotton fabric.  The cap crown is composed of six triangular panels that are sewn "
    "together, and features a textile-covered button on top and six vents.\tGRI 2(a), HTSUSA, states that “Any "
    "reference in a heading to an article shall be taken to include ...”\tThe applicable subheading for the cap "
    "crown will be 6505.90.2060, Harmonized Tariff Schedule of the United States (HTS)."
)


def test_facts_keep_the_product_description():
    facts = extract_facts(NY_J83754)
    assert "woven 100% cotton fabric" in facts
    assert "six triangular panels" in facts


def test_facts_drop_header_and_legal_analysis():
    facts = extract_facts(NY_J83754)
    for leak in ("CLA-2-65", "TARIFF NO", "GRI", "applicable subheading", "J83754"):
        assert leak not in facts


def test_strip_removes_every_form_of_the_answer_code():
    text = ("classified in 6505.90.2060, i.e. 6505902060, heading 6505, subheading 6505.90, "
            "chapter 65, Chapter 65 notes, CLA-2-65, see NY N351214 and HQ 086616, model 6505")
    stripped = strip_codes(text, ["6505.90.2060"])
    for fragment in answer_fragments(["6505.90.2060"]):
        assert fragment not in re.sub(r"\D", " ", stripped).split()
    assert "N351214" not in stripped and "086616" not in stripped
    assert "chapter" not in stripped.lower()


def test_strip_keeps_ordinary_numbers():
    stripped = strip_codes("100% cotton, 6 panels, 2003, 12 units", ["6505.90.2060"])
    assert "100%" in stripped and "2003" in stripped and "12 units" in stripped


def test_answer_fragments_cover_heading_to_statistical_suffix():
    assert answer_fragments(["6505.90.2060"]) == {"6505", "650590", "65059020", "6505902060"}
