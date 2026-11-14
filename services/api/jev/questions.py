"""Builders for Jev questions: criterion decisions, per-utterance guards, band extraction (spec §8.2, §8.3, §8.6)."""
from ..matching.extract import MED_CLASSES

CHOICE_CRITERIA = {
    "inclusion": {
        "met": "The information clearly shows the patient satisfies the criterion.",
        "not_met": "The information clearly shows the patient does NOT satisfy the criterion.",
        "unknown": "The information does not say enough to decide.",
    },
    "exclusion": {
        "met": "The information clearly shows this exclusion condition APPLIES to the patient.",
        "not_met": "The information clearly shows this exclusion condition does NOT apply to the patient.",
        "unknown": "The information does not say enough to decide.",
    },
}


def criterion_question(kind: str, text: str) -> dict:
    return {
        "type": "choice",
        "instructions": f"Does the patient satisfy this clinical-trial {kind.upper()} criterion? Criterion: «{text}»",
        "criteria": CHOICE_CRITERIA[kind],
    }


GUARDS = {
    "urgent_symptom": "Does the caller describe chest pain, stroke signs, severe low or high blood sugar symptoms, suicidal thoughts or another emergency happening NOW?",
    "wants_to_stop": "Does the caller want to stop or end the call?",
    "withdraw_consent": "Is the caller withdrawing consent or asking for their data to be removed or deleted?",
    "answered_the_question": "Did the caller's reply actually answer the question that was asked (including a clear 'I don't know')?",
    "asks_medical_advice": "Is the caller asking for medical advice, such as what treatment or dose they should take?",
}


def guard_questions() -> dict:
    return {f"g__{k}": {"type": "noul", "instructions": v} for k, v in GUARDS.items()}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def _choice(instructions: str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


_UNK = "The reply does not say, or the caller does not know."


def extract_questions(topic: str) -> dict:
    """Band/class extraction from one spoken answer. Keys are `x__<field>`."""
    if topic == "age_sex":
        return {"x__sex": _choice("What sex does the caller report?", {"male": "Male / man.", "female": "Female / woman.", "unknown": _UNK})}
    if topic == "diagnosis_duration":
        return {"x__dx": _choice("What diabetes diagnosis does the caller report?", {
            "type2": "Diagnosed with type 2 diabetes.", "type1": "Diagnosed with type 1 diabetes.",
            "none": "No diabetes diagnosis (including pre-diabetes only).", "unknown": _UNK})}
    if topic == "diabetes_meds":
        names = {"metformin": "metformin", "sulfonylurea": "a sulfonylurea such as gliclazide or glimepiride",
                 "dpp4": "a DPP-4 inhibitor (a 'gliptin' such as sitagliptin)", "sglt2": "an SGLT2 inhibitor (a 'gliflozin' such as dapagliflozin or empagliflozin)",
                 "glp1": "a GLP-1 medicine such as semaglutide (Ozempic, Rybelsus), dulaglutide, liraglutide or tirzepatide (Mounjaro)",
                 "insulin": "insulin", "tzd": "pioglitazone"}
        q = {f"x__med_{c}": noul(f"Does the caller say they currently take {names[c]}?") for c in MED_CLASSES}
        q["x__med_none"] = noul("Does the caller say they take no diabetes medicines at all (diet and lifestyle only)?")
        return q
    if topic == "hba1c":
        return {"x__band": _choice("Which band is the caller's most recent HbA1c in?", {
            "lt_7": "Below 7.0% (below 53 mmol/mol).", "7_8": "7.0% to 7.9% (53 to 63 mmol/mol).",
            "8_10": "8.0% to 9.9% (64 to 85 mmol/mol).", "gt_10": "10.0% or above (86 mmol/mol or above).", "unknown": _UNK})}
    if topic == "weight_bmi":
        return {"x__band": _choice("Which BMI band is the caller in (use height and weight if BMI is not stated)?", {
            "lt_25": "BMI below 25.", "25_30": "BMI 25 to 29.9.", "30_35": "BMI 30 to 34.9.", "35_40": "BMI 35 to 39.9.", "gt_40": "BMI 40 or above.", "unknown": _UNK})}
    if topic == "kidney":
        return {"x__band": _choice("What does the caller report about kidney disease?", {
            "none": "No kidney problems.", "mild": "Mild or early kidney problems (stage 1-2).", "ckd3": "Moderate chronic kidney disease (about stage 3).",
            "severe": "Severe kidney disease: stage 4-5, dialysis or transplant.", "unknown": _UNK})}
    if topic == "cardio_recent":
        return {"x__band": _choice("Has the caller ever had a heart attack, stroke/TIA or a heart-failure hospital admission?", {
            "none": "No, never.", "event": "Yes, at least one such event.", "unknown": _UNK})}
    if topic == "cancer_recent":
        return {"x__band": _choice("Has the caller had cancer or cancer treatment in the last five years?", {
            "none": "No.", "event": "Yes.", "unknown": _UNK})}
    if topic == "pregnancy":
        return {"x__band": _choice("What does the caller report about pregnancy?", {
            "no": "Not pregnant, not breastfeeding, not planning a pregnancy.", "pregnant": "Currently pregnant.", "breastfeeding": "Currently breastfeeding.",
            "planning": "Planning or trying for a pregnancy.", "not_applicable": "Not applicable (male, post-menopausal, hysterectomy).", "unknown": _UNK})}
    if topic == "other_trial_recent":
        return {"x__band": _choice("Is the caller in another research study?", {
            "no": "No, not now and not in the last three months.", "current": "Yes, currently taking part in another study.",
            "recent": "Finished another study within the last three months.", "unknown": _UNK})}
    return {}


ETHNICITY_CHOICES = {
    "Asian": "Asian, Asian British or Asian Welsh (e.g. Indian, Pakistani, Bangladeshi, Chinese).",
    "Black": "Black, Black British, Caribbean or African.",
    "Mixed": "Mixed or multiple ethnic groups.",
    "White": "White (British, Irish, Polish, any other White background).",
    "Other": "Any other ethnic group (e.g. Arab).",
    "unknown": "Skipped, declined, or unclear.",
}


def ethnicity_question() -> dict:
    return {"x__eth": _choice("Which high-level Census 2021 ethnic group does the caller describe themselves as?", ETHNICITY_CHOICES)}
