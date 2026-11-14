"""Intake topics (spec §8.3): order, questions, and the keyword tagger used by the rule-based atomizer."""
import re

# Fixed order = tie-break order for adaptive questioning.
TOPIC_ORDER = [
    "age_sex", "location_travel", "diagnosis_duration", "diabetes_meds", "hba1c", "weight_bmi",
    "kidney", "cardio_recent", "cancer_recent", "pregnancy", "other_trial_recent",
]
FIXED_FIRST = ["age_sex", "location_travel"]  # needed by the deterministic hard filters
ASKABLE = set(TOPIC_ORDER)
# Things that can change between intake and a later recall call.
CHANGEABLE = ["diabetes_meds", "cardio_recent", "other_trial_recent", "pregnancy", "hba1c"]

TOPICS = {
    "age_sex": {
        "label": "Age & sex",
        "question_en": "To start: how old are you, and are you male or female?",
        "question_alt": "Sorry, I missed that. What is your age in years, and your sex?",
    },
    "location_travel": {
        "label": "Location",
        "question_en": "What is the first half of your postcode? For example, N W 3.",
        "question_alt": "Just the first part of your postcode is fine, like S E 15 or M 14.",
    },
    "diagnosis_duration": {
        "label": "Diagnosis",
        "question_en": "Have you been diagnosed with type 2 diabetes, and roughly how long ago?",
        "question_alt": "Has a doctor told you that you have type 2 diabetes? About how many years ago was that?",
    },
    "diabetes_meds": {
        "label": "Diabetes medicines",
        "question_en": "Which diabetes medicines do you take at the moment? For example metformin, gliclazide, a weekly injection, or insulin.",
        "question_alt": "What do you currently take for your diabetes? Tablets, injections, insulin, or nothing?",
    },
    "hba1c": {
        "label": "HbA1c",
        "question_en": "Do you know your most recent HbA1c result? A number like 58, or a percentage like 7.5, is fine. Or just say you don't know.",
        "question_alt": "Your HbA1c is the long-term blood sugar number from your blood test. Do you remember it, roughly?",
    },
    "weight_bmi": {
        "label": "Weight / BMI",
        "question_en": "Do you know your BMI? If not, your height and weight will do.",
        "question_alt": "Roughly how tall are you, and how much do you weigh?",
    },
    "kidney": {
        "label": "Kidneys",
        "question_en": "Has a doctor ever told you that you have kidney problems? If so, how serious are they?",
        "question_alt": "Do you have kidney disease, for example a C K D stage, or are you on dialysis?",
    },
    "cardio_recent": {
        "label": "Heart & stroke",
        "question_en": "Have you ever had a heart attack, a stroke, or a hospital stay for heart failure? If yes, roughly when?",
        "question_alt": "Any heart attack or stroke in the past? About how many months or years ago?",
    },
    "cancer_recent": {
        "label": "Cancer",
        "question_en": "Have you had cancer or cancer treatment in the last five years?",
        "question_alt": "Any cancer diagnosis or treatment? If yes, roughly when?",
    },
    "pregnancy": {
        "label": "Pregnancy",
        "question_en": "Are you pregnant, breastfeeding, or planning a pregnancy?",
        "question_alt": "Is there any chance you are pregnant or breastfeeding at the moment?",
    },
    "other_trial_recent": {
        "label": "Other studies",
        "question_en": "Are you taking part in another research study now, or have you in the last three months?",
        "question_alt": "Have you been in any other clinical trial recently?",
    },
}

RECHECK_QUESTIONS = {
    "diabetes_meds": "Since we last spoke, have your diabetes medicines changed? If so, what do you take now?",
    "cardio_recent": "Since we last spoke, have you had a heart attack, a stroke, or a hospital stay for your heart?",
    "other_trial_recent": "Have you joined any other research study since we last spoke?",
    "pregnancy": "Has anything changed regarding pregnancy or breastfeeding since we last spoke?",
    "hba1c": "Have you had a new HbA1c blood test since we last spoke? If so, what was the result?",
}

_F = re.I
# Order matters: first match wins.
_TAG_RULES: list[tuple[str, re.Pattern]] = [
    ("other", re.compile(r"hypersensitiv|allerg|intoleran|contraindicat", _F)),
    ("weight_bmi", re.compile(r"^\W*(participants? must have[^:]{0,30}:\s*)?(have (a )?)?(bmi|body mass index)\b", _F)),
    ("other_trial_recent", re.compile(
        r"(participat\w*|enrol\w*|taking part|involved|included)\b.{0,80}\b(another|other|any|interventional|clinical|research)\b.{0,50}\b(trial|study|studies|research)"
        r"|investigational (drug|product|medicinal|agent|device)|other (interventional |clinical )+(trial|study|research)", _F)),
    ("pregnancy", re.compile(r"pregnan|breast[- ]?feed|lactat|child[- ]?bearing|contracepti|nursing (mother|women)", _F)),
    ("hba1c", re.compile(r"hba1c|hb a1c|glycated ha?emoglobin|glycosylated ha?emoglobin|\ba1c\b", _F)),
    ("kidney", re.compile(r"\begfr\b|\brenal\b|kidney|dialysis|creatinine|nephropath|glomerular|\bckd\b", _F)),
    ("cardio_recent", re.compile(
        r"myocardial infarction|heart attack|\bstroke\b|transient isch|\btia\b|heart failure|unstable angina|coronary"
        r"|cardiovascular (event|disease)|cardiac (event|condition|disease)|revasculari[sz]ation|\bnyha\b|acute coronary", _F)),
    ("cancer_recent", re.compile(r"cancer|malignan|neoplas|carcinoma|chemotherap|oncolog", _F)),
    ("diabetes_meds", re.compile(
        r"insulin|metformin|sulph?onylurea|gliclazide|glp-?1|sglt-?2|dpp-?4|gliptin|gliflozin|semaglutide|liraglutide|tirzepatide"
        r"|dulaglutide|exenatide|thiazolidinedione|pioglitazone|glucose[- ]lowering|glycaemic[- ]lowering|glycemic[- ]lowering"
        r"|anti-?diabetic|anti-?hyperglyc|oral hypoglyc|diabetes medic", _F)),
    ("weight_bmi", re.compile(r"\bbmi\b|body mass index|body ?weight|\bobes|overweight|weight (loss|change|gain)|bariatric|\bkg\b", _F)),
    ("diagnosis_duration", re.compile(
        r"type (2|ii|two) diabet|type (1|i|one) diabet|\bt2dm?\b|\bt1dm?\b|diabetes mellitus|diagnos\w+ (of|with) (type|diabetes)"
        r"|duration of diabetes|ketoacidosis|\bmody\b|gestational diabetes|\bdiabet", _F)),
    ("age_sex", re.compile(
        r"\baged?\b.{0,25}\d|\d+\s*(years|yrs) (of age|old)|years of age|\b(males?|females?|men|women|adults?)\b.{0,30}\b(aged?|\d)|\b(male or female|men and women|either sex)\b", _F)),
    ("location_travel", re.compile(r"(able|willing|ability) to (attend|travel)|live within|resident (of|in)", _F)),
]
_CLINICAL = re.compile(r"diabet|hba1c|bmi|insulin|surgery|disease|diagnos|treat|histor|weight|pregnan|consent", _F)


def tag_topic(text: str) -> str:
    for topic, rx in _TAG_RULES:
        if rx.search(text):
            return topic
    return "other"


def is_hard_filter(text: str, topic: str) -> bool:
    """Age/sex-only criteria are handled deterministically (spec §7.2)."""
    return topic == "age_sex" and len(text) < 110 and not _CLINICAL.search(text)
