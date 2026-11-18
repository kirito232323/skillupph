import os
import json
import re
import io
import base64
import urllib.request
import urllib.error
import urllib.parse
from django.conf import settings
from django.utils import timezone
from .models import (
    Profile, CentralizedSkill, ApplicantSkill, WorkExperience,
    Education, Certification, JobVacancy, JobSkillRequirement, TrainingProgram
)

GEMINI_API_KEY = getattr(settings, 'GEMINI_API_KEY', os.environ.get('GEMINI_API_KEY', ''))
GEMINI_MODEL = getattr(settings, 'GEMINI_MODEL', os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite'))


def call_gemini_api(prompt, system_instruction=None, json_mode=True, timeout=25, attachment=None):
    """
    Direct HTTPS REST call to Google Gemini API with JSON mode or plain text.
    `attachment` is an optional (mime_type, bytes) tuple for images/PDFs.
    Returns parsed dictionary if json_mode=True, or raw string if False.
    Returns None if API key is missing or request fails.
    """
    if not GEMINI_API_KEY:
        print("[AI Service] GEMINI_API_KEY is not configured.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    contents = []
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTION: {system_instruction}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood. I will strictly follow these instructions."}]})

    contents.append({"role": "user", "parts": [{"text": prompt}]})

    if attachment:
        mime_type, file_bytes = attachment
        contents[-1]["parts"].append({
            "inline_data": {
                "mimeType": mime_type,
                "data": base64.b64encode(file_bytes).decode("ascii"),
            }
        })

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
        }
    }

    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    data_bytes = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            candidates = res_json.get('candidates', [])
            if not candidates:
                return None
            parts = candidates[0].get('content', {}).get('parts', [])
            if not parts:
                return None
            raw_text = parts[0].get('text', '').strip()

            if json_mode:
                # Clean any markdown code blocks if present
                clean_text = raw_text
                if clean_text.startswith("```json"):
                    clean_text = clean_text[7:]
                elif clean_text.startswith("```"):
                    clean_text = clean_text[3:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
                clean_text = clean_text.strip()
                return json.loads(clean_text)
            return raw_text

    except Exception as e:
        print(f"[AI Service Error] Gemini API call failed: {e}")
        return None


# =========================================================================
# DOCUMENT PARSING & RESUME TEXT EXTRACTION
# =========================================================================

def extract_text_from_document(file_obj_or_path):
    """
    Extracts text from PDF, DOCX, or TXT document file or file path.
    """
    extracted_text = ""

    # Check if string path or file-like object
    if isinstance(file_obj_or_path, str):
        if "\n" in file_obj_or_path or not os.path.exists(file_obj_or_path):
            return file_obj_or_path.strip()

        file_path = file_obj_or_path
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.pdf':
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                for page in reader.pages:
                    txt = page.extract_text()
                    if txt:
                        extracted_text += txt + "\n"
            except Exception as e:
                print(f"[Document Extract Error] PDF parsing error: {e}")
        elif ext in ['.docx', '.doc']:
            try:
                import docx
                doc = docx.Document(file_path)
                for p in doc.paragraphs:
                    if p.text:
                        extracted_text += p.text + "\n"
                for table in doc.tables:
                    for row in table.rows:
                        row_txt = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                        if row_txt:
                            extracted_text += row_txt + "\n"
            except Exception as e:
                print(f"[Document Extract Error] DOCX parsing error: {e}")
        else:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    extracted_text = f.read()
            except Exception as e:
                print(f"[Document Extract Error] Text file read error: {e}")
    else:
        # File-like object (UploadedFile)
        name = getattr(file_obj_or_path, 'name', '').lower()
        content = file_obj_or_path.read()
        file_obj_or_path.seek(0)  # reset pointer

        if name.endswith('.pdf'):
            try:
                import pypdf
                pdf_file = io.BytesIO(content)
                reader = pypdf.PdfReader(pdf_file)
                for page in reader.pages:
                    txt = page.extract_text()
                    if txt:
                        extracted_text += txt + "\n"
            except Exception as e:
                print(f"[Document Extract Error] In-memory PDF error: {e}")
        elif name.endswith('.docx') or name.endswith('.doc'):
            try:
                import docx
                doc_file = io.BytesIO(content)
                doc = docx.Document(doc_file)
                for p in doc.paragraphs:
                    if p.text:
                        extracted_text += p.text + "\n"
                for table in doc.tables:
                    for row in table.rows:
                        row_txt = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                        if row_txt:
                            extracted_text += row_txt + "\n"
            except Exception as e:
                print(f"[Document Extract Error] In-memory DOCX error: {e}")
        else:
            try:
                extracted_text = content.decode('utf-8', errors='ignore')
            except Exception as e:
                print(f"[Document Extract Error] Plaintext decode error: {e}")

    return extracted_text.strip()


def _resume_sections(lines):
    """Split a plain-text resume into upper-case heading -> content lines.

    The parser only needs the common headings found in Philippine resumes and
    PESO intake forms. Unknown headings are ignored instead of guessed.
    """
    heading_aliases = {
        "SUMMARY": "summary",
        "PROFESSIONAL SUMMARY": "summary",
        "CAREER SUMMARY": "summary",
        "OBJECTIVE": "summary",
        "CORE SKILLS": "skills",
        "SKILLS": "skills",
        "TECHNICAL SKILLS": "skills",
        "COMPETENCIES": "skills",
        "WORK EXPERIENCE": "experience",
        "EXPERIENCE": "experience",
        "EMPLOYMENT HISTORY": "experience",
        "WORK HISTORY": "experience",
        "EDUCATION": "education",
        "EDUCATIONAL BACKGROUND": "education",
        "CERTIFICATIONS": "certifications",
        "CERTIFICATES": "certifications",
        "LICENSES": "certifications",
    }
    sections = {}
    current = None
    for line in lines:
        key = line.strip().strip(":").upper()
        key = re.sub(r"\s+", " ", key)
        if key in heading_aliases:
            current = heading_aliases[key]
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _split_date_range(value):
    parts = re.split(r"\s+[–—-]\s+", value.strip())
    if len(parts) >= 2:
        return parts[0].strip(), parts[-1].strip()
    return value.strip(), ""


def parse_resume_fallback(raw_text):
    """
    Deterministic rule-based parser used when the LLM is offline or rate-limited.

    It understands the section layout of a standard resume (summary, skills,
    work experience, education, certifications) and returns the same schema as
    the Gemini parser, so the UI and profile-application code are unchanged.
    """
    data = {
        "name": "",
        "email": "",
        "phone": "",
        "preferred_job": "",
        "bio": "",
        "skills": [],
        "experiences": [],
        "education": [],
        "certifications": []
    }

    # Extract Email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw_text)
    if email_match:
        data["email"] = email_match.group(0)

    # Extract Phone
    phone_match = re.search(r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', raw_text)
    if phone_match:
        data["phone"] = phone_match.group(0).strip()

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    # Extract Name from first 3 lines
    if lines:
        first_line = lines[0]
        if len(first_line.split()) <= 5 and not any(w in first_line.lower() for w in ['resume', 'curriculum', 'cv', 'page']):
            data["name"] = first_line

    # A short second line is usually the target role / headline.
    if len(lines) > 1:
        second = lines[1].split('|')[0].strip()
        if 2 <= len(second.split()) <= 9 and '@' not in second:
            data["preferred_job"] = second

    sections = _resume_sections(lines)

    # Skills: prefer the explicit skills section, then fall back to a full scan.
    known_skills = list(CentralizedSkill.objects.values_list('name', flat=True))
    if not known_skills:
        known_skills = [
            "Welding", "SMAW", "GTAW", "Automotive Servicing", "Electrical Installation",
            "Customer Service", "Technical Support", "Data Entry", "Microsoft Excel",
            "Python", "JavaScript", "HTML/CSS", "Bookkeeping", "Housekeeping", "Food & Beverage"
        ]
    known_map = {name.strip().lower(): name for name in known_skills}

    def canonical_skill(token):
        clean = token.strip().strip('•.;|')
        if not clean:
            return None
        key = clean.lower()
        if key in known_map:
            return known_map[key]
        # Common shorthand: CSS -> CSS3, HTML -> HTML5.
        for suffix in ("3", "5", " ii", " iii"):
            if (key + suffix) in known_map:
                return known_map[key + suffix]
        # Word-boundary containment, favouring the shortest plausible name to
        # avoid mapping "CSS" to "Tailwind CSS".
        matches = [
            name for name in known_skills
            if len(name) >= 3 and re.search(r'\b' + re.escape(key) + r'\b', name.lower())
        ]
        if matches:
            return min(matches, key=len)
        return clean[:100]

    if sections.get("skills"):
        skill_blob = " , ".join(sections["skills"])
        for token in re.split(r'[,;|•\n]', skill_blob):
            canonical = canonical_skill(token)
            if canonical:
                data["skills"].append(canonical)
    else:
        for sk in known_skills:
            pattern = r'\b' + re.escape(sk) + r'\b'
            if re.search(pattern, raw_text, re.IGNORECASE):
                data["skills"].append(sk)

    data["skills"] = list(dict.fromkeys(data["skills"]))

    if sections.get("summary"):
        data["bio"] = " ".join(sections["summary"])[:600]
    if not data["bio"]:
        data["bio"] = (
            "Experienced candidate with background in "
            f"{', '.join(data['skills'][:4]) if data['skills'] else 'vocational and technical competencies'}."
        )
    if not data["preferred_job"] and data["skills"]:
        data["preferred_job"] = f"{data['skills'][0]} Specialist"

    # Work experience lines look like:
    #   Position - Company (June 2022 - Present)
    #   Position - Company
    # Any following non-entry line is treated as the role description.
    current_entry = None
    for line in sections.get("experience", []):
        match = re.match(r"^(.+?)\s+[–—-]\s+(.+?)\s*\((.*?)\)\s*$", line)
        if not match:
            match = re.match(r"^(.+?)\s+[–—-]\s+(.+)$", line)
        if match and len(match.group(1).split()) <= 12:
            position = match.group(1).strip()
            company = match.group(2).strip()
            date_range = match.group(3).strip() if match.lastindex and match.lastindex >= 3 else ""
            start_date, end_date = _split_date_range(date_range)
            current_entry = {
                "position": position,
                "company": company,
                "start_date": start_date,
                "end_date": end_date or "Present",
                "description": "",
            }
            data["experiences"].append(current_entry)
        elif current_entry is not None:
            current_entry["description"] = (current_entry["description"] + " " + line).strip()[:500]

    # Education lines look like:
    #   BS Information Technology - University (2018 - 2022)
    for line in sections.get("education", []):
        match = re.match(r"^(.+?)\s+[–—-]\s+(.+?)\s*\((.*?)\)\s*$", line)
        if not match:
            match = re.match(r"^(.+?)\s+[–—-]\s+(.+)$", line)
        if not match:
            continue
        degree = match.group(1).strip()
        institution = match.group(2).strip()
        date_range = match.group(3).strip() if match.lastindex and match.lastindex >= 3 else ""
        years = re.findall(r"(19\d{2}|20\d{2})", date_range)
        data["education"].append({
            "institution": institution,
            "degree": degree,
            "field_of_study": degree,
            "start_year": int(years[0]) if years else 2018,
            "end_year": int(years[-1]) if len(years) >= 2 else (int(years[0]) + 4 if years else 2022),
        })

    # Certification lines: Name - Issuer (Year)
    for line in sections.get("certifications", []):
        match = re.match(r"^(.+?)\s+[–—-]\s+(.+?)(?:\s*\((.*?)\))?\s*$", line)
        if not match:
            continue
        name = match.group(1).strip()
        issuer = match.group(2).strip()
        data["certifications"].append({
            "name": name,
            "issuing_organization": issuer,
            "is_tesda": bool(re.search(r"tesda|nc\s*(i|ii|iii)", (name + " " + issuer), re.IGNORECASE)),
        })

    return data


def _normalize_id_result(raw):
    """Trim and normalize the fields returned by the ID extractor."""
    empty = {
        "id_type": "", "id_number": "", "first_name": "", "last_name": "",
        "middle_name": "", "full_name": "", "birthdate": "", "address": "",
        "sex": "", "confidence": 0,
    }
    if not isinstance(raw, dict):
        return empty

    def text(key):
        value = raw.get(key, "")
        if value is None:
            return ""
        return str(value).strip()[:300]

    full_name = text("full_name") or " ".join(
        part for part in [text("first_name"), text("middle_name"), text("last_name")] if part
    )
    confidence = raw.get("confidence", 0)
    try:
        confidence = max(0, min(100, int(float(confidence))))
    except (TypeError, ValueError):
        confidence = 0

    birthdate = text("birthdate")
    # Normalize common MM/DD/YYYY and Month DD, YYYY formats to YYYY-MM-DD.
    if birthdate:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                from datetime import datetime as _datetime
                birthdate = _datetime.strptime(birthdate, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    return {
        "id_type": text("id_type"),
        "id_number": text("id_number"),
        "first_name": text("first_name"),
        "last_name": text("last_name"),
        "middle_name": text("middle_name"),
        "full_name": full_name,
        "birthdate": birthdate,
        "address": text("address"),
        "sex": text("sex"),
        "confidence": confidence,
    }


def _fallback_id_fields(raw_text):
    """Best-effort extraction when AI is unavailable (PDF text or OCR output)."""
    result = {
        "id_type": "", "id_number": "", "first_name": "", "last_name": "",
        "middle_name": "", "full_name": "", "birthdate": "", "address": "",
        "sex": "", "confidence": 0,
    }
    if not raw_text:
        return result

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    # ID number: prefer text after "No.", "ID No", "License No", etc.
    number_match = re.search(
        r"(?:id\s*(?:no\.?|number)|license\s*(?:no\.?|number)|no\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s]{5,24})",
        raw_text, re.IGNORECASE,
    )
    if number_match:
        result["id_number"] = number_match.group(1).strip()
    else:
        generic = re.search(r"\b[A-Z]{0,3}[-\s]?\d{3,4}[-\s]?\d{3,6}[-\s]?\d{3,6}\b", raw_text)
        if generic:
            result["id_number"] = generic.group(0).strip()

    # Birthdate
    date_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", raw_text)
    if date_match:
        month, day, year = date_match.groups()
        result["birthdate"] = f"{year}-{int(month):02d}-{int(day):02d}"
    else:
        word_date = re.search(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4}\b", raw_text, re.IGNORECASE,
        )
        if word_date:
            from datetime import datetime as _datetime
            try:
                result["birthdate"] = _datetime.strptime(
                    word_date.group(0).replace(",", ""), "%B %d %Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Name: first line with 2-5 alphabetic words that is not a document header.
    skip_words = (
        "republic", "philippines", "passport", "driver", "license", "umid",
        "philhealth", "social security", "tin", "voter", "prc", "postal",
        "barangay", "senior citizen", "pwd", "identification", "card", "number",
    )
    for line in lines[:12]:
        clean = re.sub(r"[^A-Za-zñÑ\s,.'-]", "", line).strip(" ,")
        words = [w for w in re.split(r"\s+", clean) if w]
        if 2 <= len(words) <= 5 and len(clean) >= 5 and not any(s in clean.lower() for s in skip_words):
            result["full_name"] = clean.title()
            result["first_name"] = words[0].title()
            result["last_name"] = words[-1].title()
            if len(words) > 2:
                result["middle_name"] = words[1].title()
            break

    # Address: line beginning with Address or containing common address words.
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("address") or any(
            token in lowered for token in ["street", "st.", "barangay", "city", "municipality", "province"]
        ):
            result["address"] = re.sub(r"^address\s*[:\-]?\s*", "", line, flags=re.IGNORECASE).strip()[:300]
            break

    result["confidence"] = 35 if (result["id_number"] or result["full_name"]) else 0
    return result


def extract_id_document(file_obj):
    """
    Extracts identity fields from a Philippine ID image or PDF.

    Returns a dict with id_type, id_number, first_name, last_name, middle_name,
    full_name, birthdate (YYYY-MM-DD), address, sex and confidence (0-100).
    Uses Gemini vision when configured, then falls back to local text parsing.
    """
    name = getattr(file_obj, "name", "") or ""
    ext = os.path.splitext(name)[1].lower()
    file_obj.seek(0)
    payload = file_obj.read()
    file_obj.seek(0)

    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(ext, "")

    raw_text = ""
    if ext == ".pdf":
        try:
            raw_text = extract_text_from_document(file_obj)
            file_obj.seek(0)
        except Exception:
            raw_text = ""

    prompt = (
        "You are reading a Philippine government-issued identification card or document. "
        "Extract only the information that is clearly visible. Return a JSON object with "
        "these keys: id_type, id_number, first_name, last_name, middle_name, full_name, "
        "birthdate (YYYY-MM-DD), address, sex, confidence (0-100). Use an empty string for "
        "any field that is not visible. Never invent or guess values. For the name, keep the "
        "spelling exactly as printed. " + (f"Visible text: {raw_text}" if raw_text else "")
    )

    attachment = None
    if mime_type and (ext != ".pdf" or len(raw_text) < 40):
        attachment = (mime_type, payload)

    ai_result = call_gemini_api(
        prompt,
        system_instruction=(
            "You are a careful OCR and identity-document parser for a Philippine public "
            "employment office. Accuracy matters more than completeness."
        ),
        json_mode=True,
        timeout=30,
        attachment=attachment,
    )
    cleaned = _normalize_id_result(ai_result) if ai_result else None
    fallback = _fallback_id_fields(raw_text)

    if cleaned and (cleaned["id_number"] or cleaned["full_name"] or cleaned["first_name"]):
        # Fill any gaps from the local parser.
        for key, value in fallback.items():
            if not cleaned.get(key) and value:
                cleaned[key] = value
        return cleaned
    return fallback


def scan_and_extract_resume(file_obj_or_path):
    """
    Scans a resume document (PDF, Word, TXT) and returns structured applicant data.
    Uses Gemini AI with fallback to local rule-based extractor.
    """
    raw_text = extract_text_from_document(file_obj_or_path)
    if not raw_text or len(raw_text.strip()) < 20:
        return {"error": "Could not extract readable text from this document. Please ensure it is a valid PDF, Word (.docx), or text document."}

    # Truncate text if excessively long (keep first 8,000 characters)
    sample_text = raw_text[:8000]

    system_prompt = (
        "You are an expert HR and PESO (Public Employment Service Office) talent analyst. "
        "Your task is to parse resumes and extract clean, highly structured JSON data for an employment and vocational skill tracking platform."
    )

    user_prompt = f"""
Analyze the following resume text and extract the candidate's professional profile strictly as a JSON object with this exact schema:

{{
  "name": "Full Name",
  "email": "email@example.com",
  "phone": "+63 9XX XXX XXXX",
  "preferred_job": "Target / Current Job Role (e.g. Shielded Metal Arc Welder, Full Stack Developer, Call Center Representative)",
  "bio": "A professional 2-3 sentence executive career summary based on their background",
  "skills": ["Skill 1", "Skill 2", "Skill 3"],
  "experiences": [
    {{
      "position": "Job Title",
      "company": "Company Name",
      "start_date": "Year or Month Year",
      "end_date": "Year or Present",
      "description": "Brief description of duties and achievements"
    }}
  ],
  "education": [
    {{
      "institution": "School or University Name",
      "degree": "Degree or Course Name (e.g. BS Information Technology, High School Diploma, Vocational NC-II)",
      "field_of_study": "Major or Field",
      "start_year": 2018,
      "end_year": 2022
    }}
  ],
  "certifications": [
    {{
      "name": "Certificate Title (e.g. SMAW NC II, AWS Certified Cloud Practitioner)",
      "issuing_organization": "TESDA, Certiport, etc.",
      "is_tesda": true
    }}
  ]
}}

RESUME TEXT:
\"\"\"
{sample_text}
\"\"\"
"""

    ai_result = call_gemini_api(user_prompt, system_instruction=system_prompt, json_mode=True)
    if ai_result and isinstance(ai_result, dict) and ai_result.get("skills") is not None:
        ai_result["ai_powered"] = True
        return ai_result

    # Fallback to smart local parser
    fallback_result = parse_resume_fallback(raw_text)
    fallback_result["ai_powered"] = False
    fallback_result["raw_text_snippet"] = raw_text[:200]
    return fallback_result


def apply_parsed_resume_to_profile(profile, parsed_data, overwrite=False):
    """
    Applies extracted resume information into the Django database models for an Applicant Profile.
    Updates Profile, User, ApplicantSkill, WorkExperience, Education, and Certification models.
    """
    summary = {
        "skills_added": 0,
        "skills_existing": 0,
        "experiences_added": 0,
        "education_added": 0,
        "certifications_added": 0,
        "profile_updated": False,
        "details": []
    }

    if not profile or not parsed_data:
        return summary

    user = profile.user

    # 1. Update Personal & Profile Details
    extracted_name = parsed_data.get("name", "").strip()
    if extracted_name and (overwrite or not user.first_name or user.first_name.lower() in ['applicant', 'candidate', 'user']):
        parts = extracted_name.split()
        if len(parts) >= 2:
            user.first_name = parts[0]
            user.last_name = " ".join(parts[1:])
        elif len(parts) == 1:
            user.first_name = parts[0]
        user.save(update_fields=['first_name', 'last_name'])
        summary["profile_updated"] = True
        summary["details"].append(f"Updated candidate name to {user.get_full_name()}")

    extracted_phone = parsed_data.get("phone", "").strip()
    if extracted_phone and (overwrite or not profile.phone_number):
        profile.phone_number = extracted_phone
        summary["profile_updated"] = True

    extracted_preferred_job = parsed_data.get("preferred_job", "").strip()
    if extracted_preferred_job and (overwrite or not profile.preferred_job or profile.preferred_job.lower() in ['any', 'general']):
        profile.preferred_job = extracted_preferred_job
        summary["profile_updated"] = True
        summary["details"].append(f"Set preferred career to {extracted_preferred_job}")

    extracted_bio = parsed_data.get("bio", "").strip()
    if extracted_bio:
        if overwrite or not profile.title:
            profile.title = extracted_bio[:100]
        if overwrite or not profile.soft_notes:
            profile.soft_notes = extracted_bio
        summary["profile_updated"] = True

    profile.save()

    # 2. Add Skills
    extracted_skills = parsed_data.get("skills", [])
    if isinstance(extracted_skills, list):
        for sk_name in extracted_skills:
            clean_name = str(sk_name).strip()
            if not clean_name or len(clean_name) < 2:
                continue

            # Look up or create CentralizedSkill
            skill_obj = CentralizedSkill.objects.filter(name__iexact=clean_name).first()
            if not skill_obj:
                skill_obj = CentralizedSkill.objects.create(
                    name=clean_name,
                    category="Technical" if any(w in clean_name.lower() for w in ['dev', 'sql', 'python', 'weld', 'repair', 'electric']) else "Vocational"
                )

            # Check if applicant already has this skill
            existing_app_skill = ApplicantSkill.objects.filter(profile=profile, skill=skill_obj).first()
            if not existing_app_skill:
                ApplicantSkill.objects.create(
                    profile=profile,
                    skill=skill_obj,
                    source="AI Resume Scan"
                )
                summary["skills_added"] += 1
            else:
                summary["skills_existing"] += 1

    # 3. Add Work Experience
    extracted_experiences = parsed_data.get("experiences", [])
    if isinstance(extracted_experiences, list):
        for exp in extracted_experiences:
            position = str(exp.get("position", "")).strip()
            company = str(exp.get("company", "")).strip()
            if position or company:
                # Avoid duplicates
                exists = WorkExperience.objects.filter(
                    profile=profile,
                    company__iexact=company or "General Experience",
                    position__iexact=position or "Specialist"
                ).exists()
                if not exists:
                    WorkExperience.objects.create(
                        profile=profile,
                        position=position or "Specialist",
                        company=company or "Verified Employer",
                        start_date=str(exp.get("start_date", "2022"))[:50],
                        end_date=str(exp.get("end_date", "Present"))[:50],
                        description=str(exp.get("description", ""))
                    )
                    summary["experiences_added"] += 1

    # 4. Add Education
    extracted_education = parsed_data.get("education", [])
    if isinstance(extracted_education, list):
        for edu in extracted_education:
            inst = str(edu.get("institution", "")).strip()
            deg = str(edu.get("degree", "")).strip()
            if inst or deg:
                exists = Education.objects.filter(
                    profile=profile,
                    institution__iexact=inst or "Academic Institution",
                    degree__iexact=deg or "Certificate / Degree"
                ).exists()
                if not exists:
                    start_yr = edu.get("start_year")
                    end_yr = edu.get("end_year")
                    try:
                        start_yr = int(start_yr) if start_yr else 2018
                    except (ValueError, TypeError):
                        start_yr = 2018
                    try:
                        end_yr = int(end_yr) if end_yr else 2022
                    except (ValueError, TypeError):
                        end_yr = 2022

                    Education.objects.create(
                        profile=profile,
                        institution=inst or "Academic Institution",
                        degree=deg or "Certificate / Degree",
                        field_of_study=str(edu.get("field_of_study", deg))[:100],
                        start_year=start_yr,
                        end_year=end_yr
                    )
                    summary["education_added"] += 1

    # 5. Add Certifications
    extracted_certs = parsed_data.get("certifications", [])
    if isinstance(extracted_certs, list):
        for cert in extracted_certs:
            c_name = str(cert.get("name", "")).strip()
            if c_name:
                exists = Certification.objects.filter(profile=profile, name__iexact=c_name).exists()
                if not exists:
                    Certification.objects.create(
                        profile=profile,
                        name=c_name,
                        issuing_organization=str(cert.get("issuing_organization", "Accredited Body"))[:150],
                        is_tesda=bool(cert.get("is_tesda", False) or "tesda" in c_name.lower() or "nc" in c_name.lower())
                    )
                    summary["certifications_added"] += 1

    return summary


# =========================================================================
# AI FEATURE 2: SMART JOB RECOMMENDATIONS ENGINE
# =========================================================================

def get_ai_job_recommendations(profile, top_n=6):
    """
    Analyzes applicant profile and all active Job Vacancies.
    Returns ranked list of vacancies with match percentage and personalized reasoning.
    """
    try:
        from tracker.models import SystemSetting
        if not SystemSetting.get_settings().ai_job_matching:
            return []
    except Exception:
        pass

    from tracker.views import is_job_career_aligned, calculate_match_score

    all_open = list(
        JobVacancy.objects.filter(status='Open')
        .select_related('employer')
        .prefetch_related('requirements__skill')
    )
    aligned_vacs = [v for v in all_open if is_job_career_aligned(profile, v)]
    other_vacs = [v for v in all_open if not is_job_career_aligned(profile, v)]
    # Prioritize career-aligned vacancies
    vacancies = (aligned_vacs + other_vacs)[:40]
    if not vacancies:
        return []

    # Applicant data
    user_skills = list(ApplicantSkill.objects.filter(profile=profile).values_list('skill__name', flat=True))
    preferred_job = profile.preferred_job or ""
    bio = getattr(profile, 'soft_notes', '') or getattr(profile, 'title', '') or ''
    experiences = list(WorkExperience.objects.filter(profile=profile).values('position', 'company'))

    # Candidate profile summary
    candidate_summary = {
        "preferred_job": preferred_job,
        "skills": user_skills,
        "experience": experiences[:3],
        "bio": bio[:300]
    }

    # Canonical match calculation for exact percentage consistency
    from tracker.views import calculate_match_score

    # Candidate skills cache
    applicant_skills = list(ApplicantSkill.objects.filter(profile=profile).select_related('skill'))
    applicant_skill_ids = {askill.skill_id for askill in applicant_skills}
    candidate_skills_lower = {askill.skill.name.lower() for askill in applicant_skills if askill.skill}

    # Vacancy summary list for AI analysis
    vacancies_payload = []
    vac_skill_metrics = {}

    for v in vacancies:
        reqs = list(v.requirements.all())
        match_pct, gaps = calculate_match_score(profile, v)
        if gaps:
            skill_pct = int(round(sum(g.get('candidate', 0) for g in gaps) / len(gaps) * 100))
        else:
            skill_pct = 100

        exact_names = [g['skill'] for g in gaps if g.get('match_type') == 'Exact']
        transferable_names = [g['skill'] for g in gaps if g.get('match_type') == 'Transferable']
        missing_names = [g['skill'] for g in gaps if g.get('match_type') == 'None']
        matched_names = exact_names + transferable_names

        vac_skill_metrics[v.id] = {
            "matched_names": matched_names,
            "exact_names": exact_names,
            "transferable_names": transferable_names,
            "missing_names": missing_names,
            "skill_pct": skill_pct,
        }

        vacancies_payload.append({
            "id": v.id,
            "title": v.title,
            "category": v.category,
            "company": v.employer.company_name if v.employer else "Private Employer",
            "location": v.location,
            "required_skills": [req.skill.name for req in reqs if req.skill],
            "actual_skill_match_pct": skill_pct,
            "salary": v.salary_range
        })

    prompt = f"""
You are the AI Matchmaker for PESO SKILLUP Portal.
Match this job applicant against the available vacancies.

CANDIDATE PROFILE:
{json.dumps(candidate_summary, indent=2)}

OPEN VACANCIES:
{json.dumps(vacancies_payload, indent=2)}

INSTRUCTIONS:
1. Rank the top {top_n} most suitable jobs for this candidate prioritizing actual skill alignment and preferred career direction.
2. For each ranked job, provide:
   - "job_id": integer matching the vacancy ID
   - "match_score": integer percentage (must be consistent with actual_skill_match_pct)
   - "matching_skills": list of candidate skills that fit the job
   - "missing_skills": list of skills the candidate needs to learn
   - "why_you_match": 1-2 realistic, encouraging sentences explaining why this job is recommended and what skills to bridge if any.

Return STRICT JSON format matching:
{{
  "recommendations": [
    {{
      "job_id": 1,
      "match_score": 92,
      "matching_skills": ["Welding", "Blueprint Reading"],
      "missing_skills": ["Safety NC-II"],
      "why_you_match": "Your background matches core competencies with strong alignment."
    }}
  ]
}}
"""

    ai_response = call_gemini_api(prompt, json_mode=True, timeout=20)

    recommended_results = []
    vac_dict = {v.id: v for v in vacancies}

    if ai_response and isinstance(ai_response, dict) and "recommendations" in ai_response:
        recs = ai_response.get("recommendations", [])
        for r in recs:
            jid = r.get("job_id")
            if jid in vac_dict:
                v_obj = vac_dict[jid]
                metrics = vac_skill_metrics.get(jid, {"matched_names": [], "missing_names": [], "skill_pct": 0})
                
                # Ground match_score directly in canonical skill matching percentage
                final_score = metrics["skill_pct"]
                
                why_text = r.get("why_you_match", "")
                if not why_text or "Matches 0" in why_text:
                    if final_score == 0:
                        why_text = f"Aligned with your {v_obj.category or 'target'} interest. Bridge {len(metrics['missing_names'])} required skills below to qualify."
                    else:
                        why_text = f"Matches {len(metrics['matched_names'])} of verified skill requirements ({final_score}% skill fit)."

                recommended_results.append({
                    "vacancy": v_obj,
                    "match_score": final_score,
                    "matching_skills": metrics["matched_names"],
                    "skills": metrics["matched_names"],
                    "missing_skills": metrics["missing_names"] if metrics["missing_names"] else r.get("missing_skills", []),
                    "why_you_match": why_text,
                    "is_bookmarked": False,
                    "ai_powered": True
                })
        if recommended_results:
            # Sort by match_score descending so actual matches appear first
            recommended_results.sort(key=lambda x: x["match_score"], reverse=True)
            return recommended_results[:top_n]

    # Local Fallback Rule-Based Ranker grounded in exact skill match
    scored_vacs = []

    for v in vacancies:
        metrics = vac_skill_metrics.get(v.id, {"matched_names": [], "missing_names": [], "skill_pct": 0})
        matching_skills = metrics["matched_names"]
        missing_skills = metrics["missing_names"]
        score = metrics["skill_pct"]

        if score == 0:
            why_text = f"Aligned with your {v.category or 'target'} career goals. Bridge {len(missing_skills)} required skill gaps to qualify."
        else:
            why_text = f"Matches {len(matching_skills)} verified skill area{'s' if len(matching_skills) != 1 else ''} ({score}% skill fit)."

        scored_vacs.append({
            "vacancy": v,
            "match_score": score,
            "matching_skills": matching_skills,
            "skills": matching_skills,
            "missing_skills": missing_skills,
            "why_you_match": why_text,
            "is_bookmarked": False,
            "ai_powered": False
        })

    # Sort strictly by match_score descending, prioritizing career-aligned vacancies
    aligned_scored = [s for s in scored_vacs if is_job_career_aligned(profile, s["vacancy"])]
    pool = aligned_scored if aligned_scored else scored_vacs
    pool.sort(key=lambda x: x["match_score"], reverse=True)
    return pool[:top_n]


def analyze_job_qualification_fit_ai(profile, vacancy):
    """
    Performs comprehensive AI analysis evaluating candidate's qualifications and requirements 
    against a specific job vacancy using Google Gemini with deterministic fallback.
    """
    from tracker.views import calculate_match_score, get_qualification_breakdown
    
    # Pre-calculate multi-factor baseline metrics
    match_pct, gaps = calculate_match_score(profile, vacancy)
    breakdown = get_qualification_breakdown(profile, vacancy)
    
    candidate_skills = list(profile.applicant_skills.values_list('skill__name', flat=True))
    candidate_education = [f"{e.degree} at {e.institution}" for e in profile.education.all()]
    candidate_certs = [c.name for c in profile.certifications.all()]
    candidate_exp = [f"{w.position} at {w.company} ({w.description or ''})" for w in profile.experience.all()]
    
    cand_summary = {
        "full_name": profile.user.get_full_name() or profile.user.username,
        "title": profile.title or "Applicant",
        "skills": candidate_skills,
        "education": candidate_education if candidate_education else ["No formal college/degree recorded"],
        "certifications": candidate_certs if candidate_certs else ["No certifications recorded"],
        "work_experience": candidate_exp if candidate_exp else ["No recorded prior experience"],
        "experience_years": profile.experience_years or 0,
        "is_fresh_grad": profile.is_fresh_grad
    }
    
    req_skills = [r.skill.name for r in vacancy.requirements.all() if r.skill]
    job_summary = {
        "title": vacancy.title,
        "company": vacancy.employer.company_name if vacancy.employer else "Private Employer",
        "category": vacancy.category or "",
        "min_education": vacancy.min_education or "Not specified",
        "required_experience_years": vacancy.required_experience_years or 0,
        "required_skills": req_skills,
        "description": (vacancy.description or "")[:1200]
    }
    
    prompt = f"""
You are the Senior Talent Evaluator and Qualifications Assessor for the PESO SKILLUP Workforce platform.
Conduct an objective, structured Qualifications and Requirements Fit Assessment comparing this candidate against the job vacancy.

CANDIDATE PROFILE:
{json.dumps(cand_summary, indent=2)}

JOB VACANCY DETAILS & REQUIREMENTS:
{json.dumps(job_summary, indent=2)}

BASELINE MULTI-FACTOR SCORE: {int(round(match_pct))}%

INSTRUCTIONS:
1. Examine:
   - Each required skill / competency.
   - The minimum education requirement.
   - The required years of experience.
   - Specific qualifications, certifications, or duties outlined in the job description.
2. For each requirement, determine if the candidate's status is 'Met', 'Partially Met', or 'Missing'.
3. Provide:
   - "fit_percentage": integer from 0 to 100 (aligned around baseline multi-factor score {int(round(match_pct))}%)
   - "verdict_tier": one of ["Exceptional Match", "Strong Fit", "Moderate Fit with Gaps", "Significant Upskilling Needed"]
   - "executive_summary": 2-3 encouraging, realistic sentences summarizing their qualifications fit and main gap areas.
   - "checklist": list of requirement objects:
       [
         {{
           "requirement": "Requirement name",
           "category": "Core Skill" | "Education" | "Experience" | "Job Description",
           "status": "Met" | "Partially Met" | "Missing",
           "candidate_qualification": "What candidate possesses or 'None recorded'",
           "comment": "Brief concise rationale"
         }}
       ]
   - "key_strengths": list of 2-4 specific candidate strengths fitting this role.
   - "recommended_actions": list of 1-3 concrete next steps or courses to close remaining gaps.

Return STRICT JSON format only.
"""
    
    ai_res = call_gemini_api(prompt, system_instruction="You are a strict, objective HR competency auditor.", json_mode=True, timeout=20)
    
    if ai_res and isinstance(ai_res, dict) and "checklist" in ai_res and "verdict_tier" in ai_res:
        ai_res["breakdown"] = breakdown
        ai_res["ai_powered"] = True
        return ai_res
        
    # Deterministic fallback if API fails or offline
    fallback_checklist = []
    for g in gaps:
        status = "Met" if g['gap'] == 0 else "Missing"
        cand_qual = f"Verified proficiency in {g['skill']}" if g['gap'] == 0 else "Skill not yet verified in profile"
        fallback_checklist.append({
            "requirement": f"Competency in {g['skill']}",
            "category": "Core Skill",
            "status": status,
            "candidate_qualification": cand_qual,
            "comment": "Requirement met via candidate skill inventory." if g['gap'] == 0 else "Candidate has a skill deficiency in this competency."
        })
        
    edu_met = breakdown['edu_met']
    fallback_checklist.append({
        "requirement": f"Minimum Education: {vacancy.min_education or 'Not specified'}",
        "category": "Education",
        "status": "Met" if edu_met else "Partially Met",
        "candidate_qualification": breakdown['cand_edu_desc'],
        "comment": "Meets or exceeds specified educational standard." if edu_met else "Candidate education level is below the preferred minimum."
    })
    
    exp_met = breakdown['exp_met']
    req_exp = vacancy.required_experience_years or 0
    fallback_checklist.append({
        "requirement": f"Relevant Experience: {req_exp} year{'s' if req_exp != 1 else ''}" if req_exp > 0 else "Entry Level / No Prior Experience Required",
        "category": "Experience",
        "status": "Met" if exp_met else ("Partially Met" if breakdown['exp_score'] >= 50 else "Missing"),
        "candidate_qualification": f"{breakdown['cand_exp_years']} year(s) recorded",
        "comment": "Satisfies the minimum required years." if exp_met else f"Shortfall of {max(0, req_exp - breakdown['cand_exp_years'])} year(s)."
    })
    
    final_score = int(round(match_pct))
    if final_score >= 80:
        tier = "Strong Fit"
        summary = f"Candidate profile demonstrates strong alignment with {vacancy.title}, satisfying {final_score}% of core qualifications."
    elif final_score >= 60:
        tier = "Moderate Fit with Gaps"
        summary = f"Candidate has good foundational readiness ({final_score}% overall fit), with actionable gap areas that can be bridged with upskilling."
    else:
        tier = "Significant Upskilling Needed"
        summary = f"Candidate currently meets {final_score}% of the qualifications. Completing recommended training programs is suggested before applying."
        
    strengths = [f"Verified competency in {g['skill']}" for g in gaps if g['gap'] == 0]
    if not strengths:
        strengths = [f"Educational background in {breakdown['cand_edu_desc']}"]
        
    actions = [f"Enroll in training program for {g['skill']}" for g in gaps if g['gap'] > 0]
    if not actions:
        actions = ["Submit direct application through PESO portal"]
        
    return {
        "fit_percentage": final_score,
        "verdict_tier": tier,
        "executive_summary": summary,
        "checklist": fallback_checklist,
        "key_strengths": strengths[:3],
        "recommended_actions": actions[:3],
        "breakdown": breakdown,
        "ai_powered": False
    }


def get_ai_training_recommendations(profile, top_n=6):
    """
    Analyzes applicant profile, skills, work history, target jobs, and missing skill gaps.
    Returns ranked training programs with fit score and personalized career impact reasoning.
    """
    from .models import TrainingProgram, TrainingEnrollment, JobVacancy, ApplicantSkill, WorkExperience

    # Enrolled program IDs (exclude from recommendations)
    enrolled_ids = set(TrainingEnrollment.objects.filter(profile=profile).values_list('training_program_id', flat=True))

    programs = list(
        TrainingProgram.objects.exclude(id__in=enrolled_ids)
        .exclude(status='Completed')
        .select_related('skill_addressed')
        .prefetch_related('skills_addressed', 'enrollments')
    )
    if not programs:
        return []

    # Applicant data
    user_skills = list(ApplicantSkill.objects.filter(profile=profile).values_list('skill__name', flat=True))
    preferred_job = profile.preferred_job or ""
    bio = getattr(profile, 'soft_notes', '') or getattr(profile, 'title', '') or ''
    experiences = list(WorkExperience.objects.filter(profile=profile).values('position', 'company'))

    # Determine applicant's skill gaps from their bookmarked or matching vacancies
    skill_gaps = set()
    bookmarked_vacancies = JobVacancy.objects.filter(bookmarks__profile=profile).exclude(status='Closed')
    for vac in bookmarked_vacancies:
        req_skills = list(vac.requirements.values_list('skill__name', flat=True))
        for r_sk in req_skills:
            if r_sk not in user_skills:
                skill_gaps.add(r_sk)

    if not skill_gaps:
        # Fallback to category vacancies
        cat_vacancies = JobVacancy.objects.filter(category=preferred_job).exclude(status='Closed')[:5] if preferred_job else JobVacancy.objects.exclude(status='Closed')[:5]
        for vac in cat_vacancies:
            req_skills = list(vac.requirements.values_list('skill__name', flat=True))
            for r_sk in req_skills:
                if r_sk not in user_skills:
                    skill_gaps.add(r_sk)

    programs_payload = []
    for p in programs[:35]:
        all_sk_names = [s.name for s in p.all_skills]
        programs_payload.append({
            "id": p.id,
            "title": p.title,
            "provider": p.provider,
            "skills": all_sk_names,
            "description": (p.description or "")[:180],
            "duration": p.duration,
            "available_slots": p.available_slots,
            "location": p.location or "PESO Training Center"
        })

    candidate_summary = {
        "preferred_job": preferred_job,
        "skills": user_skills,
        "skill_gaps": list(skill_gaps)[:8],
        "experience": experiences[:2],
        "bio": bio[:200]
    }

    prompt = f"""
You are the AI Career & Training Counselor for PESO SKILLUP Portal.
Analyze this job applicant and recommend the best training / upskilling programs from the available catalog.

CANDIDATE PROFILE:
{json.dumps(candidate_summary, indent=2)}

AVAILABLE TRAINING PROGRAMS:
{json.dumps(programs_payload, indent=2)}

INSTRUCTIONS:
1. Select up to {top_n} most impactful training programs that directly address the candidate's skill gaps and elevate their employability.
2. For each recommended program, provide:
   - "program_id": integer matching the program ID
   - "match_score": integer percentage between 75 and 99
   - "key_skills": list of 1-3 core competencies this course provides
   - "why_recommended": 1-2 encouraging, concise sentences explaining how this course bridges their specific gap and boosts their hiring potential.

Return STRICT JSON format matching:
{{
  "recommendations": [
    {{
      "program_id": 1,
      "match_score": 96,
      "key_skills": ["CNC Multi-Axis", "Precision Tooling"],
      "why_recommended": "Directly bridges your CNC gap required by multiple manufacturing employers in Bulacan, boosting your profile match to over 90%."
    }}
  ]
}}
"""

    ai_response = call_gemini_api(prompt, json_mode=True, timeout=20)

    recommended_results = []
    prog_dict = {p.id: p for p in programs}

    if ai_response and isinstance(ai_response, dict) and "recommendations" in ai_response:
        recs = ai_response.get("recommendations", [])
        for r in recs:
            pid = r.get("program_id")
            if pid in prog_dict:
                p_obj = prog_dict[pid]
                recommended_results.append({
                    "program": p_obj,
                    "match_score": min(99, max(75, int(r.get("match_score", 85)))),
                    "key_skills": r.get("key_skills", [s.name for s in p_obj.all_skills]),
                    "why_recommended": r.get("why_recommended", f"Recommended by AI to elevate your profile for {preferred_job or 'in-demand'} roles."),
                    "ai_powered": True
                })
        if recommended_results:
            return recommended_results[:top_n]

    # Local Fallback Rule-Based Recommender if API is unavailable
    candidate_skills_lower = set(s.lower() for s in user_skills)
    skill_gaps_lower = set(s.lower() for s in skill_gaps)
    scored_programs = []

    for p in programs:
        score = 70
        p_skills = [s.name.lower() for s in p.all_skills]
        gap_hits = [sk for sk in p_skills if sk in skill_gaps_lower]
        if gap_hits:
            score += 20 + min(8, len(gap_hits) * 4)
        elif preferred_job and (preferred_job.lower() in p.title.lower() or (p.description and preferred_job.lower() in p.description.lower())):
            score += 15

        if p.available_slots > 0:
            score += 4

        display_skills = [s.name for s in p.all_skills]
        why_text = f"Addresses essential competencies for {preferred_job or 'in-demand industry roles'} and enhances your verified qualifications."
        if gap_hits:
            why_text = f"Directly bridges your '{gap_hits[0].title()}' gap, enhancing your candidacy for target vacancies."

        scored_programs.append({
            "program": p,
            "match_score": min(98, score),
            "key_skills": display_skills[:3],
            "why_recommended": why_text,
            "ai_powered": False
        })

    scored_programs.sort(key=lambda x: x["match_score"], reverse=True)
    return scored_programs[:top_n]


# =========================================================================
# =========================================================================
# CURATED VOCATIONAL & TECHNICAL VIDEO DATABASE (YouTube High-Authority)
# =========================================================================

CURATED_VOCATIONAL_VIDEOS = [
    # --- AUTOMOTIVE SERVICING NC II ---
    {
        "keywords": ["multimeter", "electrical", "voltage", "amperage", "battery", "wiring", "charging", "starter"],
        "domains": ["auto", "automotive", "mechanic", "car", "engine"],
        "video_url": "https://www.youtube.com/watch?v=COJr7OB23Hw",
        "embed_url": "https://www.youtube.com/embed/COJr7OB23Hw",
        "title": "How to Test a Car Battery with a Multimeter",
        "duration_str": "15:20 HD",
        "channel": "ChrisFix"
    },
    {
        "keywords": ["parasitic", "short", "fuse", "relay", "circuit", "draw", "leak"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=n5FJDgcdii8",
        "embed_url": "https://www.youtube.com/embed/n5FJDgcdii8",
        "title": "How to Find an Electrical Short in Your Car",
        "duration_str": "12:45 HD",
        "channel": "ChrisFix"
    },
    {
        "keywords": ["brake", "hydraulic", "caliper", "rotor", "bleeding", "abs", "pad", "drum"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=6RQ9UabOIPg",
        "embed_url": "https://www.youtube.com/embed/6RQ9UabOIPg",
        "title": "How to Replace Brake Pads and Rotors (COMPLETE Guide)",
        "duration_str": "21:10 HD",
        "channel": "ChrisFix"
    },
    {
        "keywords": ["front brake", "brake fluid", "caliper service", "flush"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=UlbFFq60Tec",
        "embed_url": "https://www.youtube.com/embed/UlbFFq60Tec",
        "title": "How to Replace Front Brakes, Pads and Rotors",
        "duration_str": "19:30 HD",
        "channel": "ChrisFix"
    },
    {
        "keywords": ["engine", "piston", "cylinder", "crankshaft", "camshaft", "timing", "overhaul", "combustion", "four-stroke"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=ZQvfHyfgBtA",
        "embed_url": "https://www.youtube.com/embed/ZQvfHyfgBtA",
        "title": "How a Car Engine Works (3D Animated Breakdown)",
        "duration_str": "11:50 HD",
        "channel": "Animagraffs"
    },
    {
        "keywords": ["obd", "diagnostic", "scanner", "dtc", "trouble code", "check engine", "sensor", "ecu", "scan tool"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=lpenIb9Yfn4",
        "embed_url": "https://www.youtube.com/embed/lpenIb9Yfn4",
        "title": "How to Use an OBD2 Scanner | Step by Step for Beginners",
        "duration_str": "14:20 HD",
        "channel": "WeeklyTools"
    },
    {
        "keywords": ["code reader", "diagnostic tool", "live data", "freeze frame"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=YGG9VLzeMk8",
        "embed_url": "https://www.youtube.com/embed/YGG9VLzeMk8",
        "title": "How to Use an OBD2 Scan Tool for Diagnostics",
        "duration_str": "10:15 HD",
        "channel": "EasyAutoFix"
    },
    {
        "keywords": ["alternator", "charging", "generator", "belt", "pulley"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=ILih7JtAo_s",
        "embed_url": "https://www.youtube.com/embed/ILih7JtAo_s",
        "title": "How To Test a Car Alternator with a Multimeter",
        "duration_str": "08:40 HD",
        "channel": "ChrisBassettDIY"
    },
    {
        "keywords": ["safety", "shop", "ppe", "hazard", "jack", "lift", "inspection", "workshop"],
        "domains": ["auto", "automotive", "mechanic", "car"],
        "video_url": "https://www.youtube.com/watch?v=Ux1wJusNUIY",
        "embed_url": "https://www.youtube.com/embed/Ux1wJusNUIY",
        "title": "Automotive Shop Safety Protocols & Workshop Rules",
        "duration_str": "14:10 HD",
        "channel": "Jonathan Smith Auto Tech"
    },

    # --- SHIELDED METAL ARC WELDING (SMAW) NC II ---
    {
        "keywords": ["arc", "stick", "smaw", "electrode", "striking", "weld", "bead", "amperage", "e6013", "e7018"],
        "domains": ["weld", "welding", "smaw", "fabrication", "metal"],
        "video_url": "https://www.youtube.com/watch?v=Dybrlx-BTXk",
        "embed_url": "https://www.youtube.com/embed/Dybrlx-BTXk",
        "title": "Stick Welding Basics: Full Tutorial (SMAW Essentials)",
        "duration_str": "16:45 HD",
        "channel": "TimWelds"
    },
    {
        "keywords": ["welding 101", "machine setup", "current", "ground clamp", "rod"],
        "domains": ["weld", "welding", "smaw", "fabrication"],
        "video_url": "https://www.youtube.com/watch?v=4MKuUICV6-c",
        "embed_url": "https://www.youtube.com/embed/4MKuUICV6-c",
        "title": "Stick Welding 101: Getting Started With SMAW",
        "duration_str": "18:20 HD",
        "channel": "Weld.com"
    },
    {
        "keywords": ["striking arc", "scratch start", "tapping", "beginner welding"],
        "domains": ["weld", "welding", "smaw"],
        "video_url": "https://www.youtube.com/watch?v=3UEn4FjeOx4",
        "embed_url": "https://www.youtube.com/embed/3UEn4FjeOx4",
        "title": "Intro to SMAW: Stick Welding for Beginners",
        "duration_str": "12:35 HD",
        "channel": "WeldFever"
    },

    # --- ELECTRICAL INSTALLATION & MAINTENANCE (EIM) NC II ---
    {
        "keywords": ["electricity", "electrical", "wiring", "voltage", "current", "circuit", "ohm"],
        "domains": ["electrical", "electrician", "wire", "circuit", "eim"],
        "video_url": "https://www.youtube.com/watch?v=mc979OhitAg",
        "embed_url": "https://www.youtube.com/embed/mc979OhitAg",
        "title": "How ELECTRICITY Works - Working Principle & Fundamentals",
        "duration_str": "10:20 HD",
        "channel": "The Engineering Mindset"
    },
    {
        "keywords": ["house wiring", "installation", "breaker", "panel", "junction", "outlet"],
        "domains": ["electrical", "electrician", "eim", "installation"],
        "video_url": "https://www.youtube.com/watch?v=y4cSaZs7tU8",
        "embed_url": "https://www.youtube.com/embed/y4cSaZs7tU8",
        "title": "House Wiring Tutorial: Basic Electrical Installation",
        "duration_str": "24:15 HD",
        "channel": "Local Electrician"
    },
    {
        "keywords": ["basic wiring", "switch", "lamp", "connection", "cable"],
        "domains": ["electrical", "electrician", "wiring"],
        "video_url": "https://www.youtube.com/watch?v=LPuCicN4ahc",
        "embed_url": "https://www.youtube.com/embed/LPuCicN4ahc",
        "title": "Home Basic Electrical Wiring Fundamentals",
        "duration_str": "15:40 HD",
        "channel": "Japegz TV"
    },

    # --- REFRIGERATION & AIR CONDITIONING (RAC / HVAC) ---
    {
        "keywords": ["aircon", "air conditioning", "hvac", "refrigeration", "compressor", "cooling", "condenser", "evaporator"],
        "domains": ["rac", "hvac", "refrigeration", "aircon", "cooling"],
        "video_url": "https://www.youtube.com/watch?v=SfuSzBja8QA",
        "embed_url": "https://www.youtube.com/embed/SfuSzBja8QA",
        "title": "Air Conditioning System Basics - HVACR How Does It Work",
        "duration_str": "16:30 HD",
        "channel": "The Engineering Mindset"
    },
    {
        "keywords": ["refrigerant", "cycle", "freon", "r134a", "r410a", "pressure", "expansion valve"],
        "domains": ["rac", "hvac", "refrigeration", "cooling"],
        "video_url": "https://www.youtube.com/watch?v=Uv3GfEQhtPE",
        "embed_url": "https://www.youtube.com/embed/Uv3GfEQhtPE",
        "title": "Basic Refrigeration Cycle - How It Works",
        "duration_str": "11:45 HD",
        "channel": "The Engineering Mindset"
    },

    # --- SOLAR PV INSTALLATION & COMMISSIONING ---
    {
        "keywords": ["solar", "photovoltaic", "pv", "inverter", "battery storage", "solar panel"],
        "domains": ["solar", "renewable", "photovoltaic", "energy"],
        "video_url": "https://www.youtube.com/watch?v=Yxt72aDjFgY",
        "embed_url": "https://www.youtube.com/embed/Yxt72aDjFgY",
        "title": "How Solar Photovoltaic (PV) Panels Generate Electricity",
        "duration_str": "12:40 HD",
        "channel": "The Engineering Mindset"
    },

    # --- PLUMBING & PIPEFITTING ---
    {
        "keywords": ["plumbing", "pipe", "pex", "copper", "fitting", "leak", "valve", "pvc"],
        "domains": ["plumbing", "pipe", "sanitary", "water"],
        "video_url": "https://www.youtube.com/watch?v=dD-Qem26i6s",
        "embed_url": "https://www.youtube.com/embed/dD-Qem26i6s",
        "title": "How to Transition Plumbing Pipes (PEX, Copper, PVC Guide)",
        "duration_str": "14:50 HD",
        "channel": "Got2Learn"
    },
    {
        "keywords": ["solder", "soldering", "torch", "flux", "copper joint"],
        "domains": ["plumbing", "pipe", "metal"],
        "video_url": "https://www.youtube.com/watch?v=JyVbjDsric4",
        "embed_url": "https://www.youtube.com/embed/JyVbjDsric4",
        "title": "How to Solder Copper Pipe The Correct Way",
        "duration_str": "11:20 HD",
        "channel": "Got2Learn"
    },

    # --- OCCUPATIONAL SAFETY & HEALTH (COSH / BOSH) ---
    {
        "keywords": ["safety", "workplace", "ppe", "osha", "cosh", "bosh", "hazard", "incident", "prevention", "ergonomics"],
        "domains": ["safety", "cosh", "bosh", "construction", "ergonomics", "health"],
        "video_url": "https://www.youtube.com/watch?v=BVCmz9KnwWk",
        "embed_url": "https://www.youtube.com/embed/BVCmz9KnwWk",
        "title": "10 Fundamental Rules For Workplace Safety",
        "duration_str": "10:30 HD",
        "channel": "ESS INFO Safety"
    },
    {
        "keywords": ["ppe", "hard hat", "boots", "vest", "goggles", "gloves", "construction safety"],
        "domains": ["safety", "construction", "osha"],
        "video_url": "https://www.youtube.com/watch?v=Q1W1YFVPHqU",
        "embed_url": "https://www.youtube.com/embed/Q1W1YFVPHqU",
        "title": "What PPE Does OSHA Require on Construction Sites",
        "duration_str": "08:15 HD",
        "channel": "Civil Engineer Juwel"
    },

    # --- FORKLIFT & HEAVY EQUIPMENT OPERATIONS ---
    {
        "keywords": ["forklift", "heavy equipment", "warehouse", "pallet", "mast", "load capacity"],
        "domains": ["forklift", "logistics", "warehouse", "equipment"],
        "video_url": "https://www.youtube.com/watch?v=fPhynD2yuBE",
        "embed_url": "https://www.youtube.com/embed/fPhynD2yuBE",
        "title": "Forklift Safety Video - OSHA Training for Operators",
        "duration_str": "15:40 HD",
        "channel": "SafetyVideos.com"
    },
    {
        "keywords": ["drive forklift", "pedestrian", "stability triangle", "inspection"],
        "domains": ["forklift", "logistics", "equipment"],
        "video_url": "https://www.youtube.com/watch?v=kkPvyYipF8E",
        "embed_url": "https://www.youtube.com/embed/kkPvyYipF8E",
        "title": "How to Drive a Forklift Safely: Operator Basics",
        "duration_str": "12:15 HD",
        "channel": "SafetyVideos.com"
    },

    # --- BPO & CUSTOMER SERVICE EXCELLENCE ---
    {
        "keywords": ["customer service", "call center", "bpo", "phone", "etiquette", "communication", "active listening", "client"],
        "domains": ["bpo", "customer", "call center", "support", "voice"],
        "video_url": "https://www.youtube.com/watch?v=MGKhCFIUwq8",
        "embed_url": "https://www.youtube.com/embed/MGKhCFIUwq8",
        "title": "10 Telephone Customer Service Tips | Telephone Etiquette",
        "duration_str": "09:50 HD",
        "channel": "Business Training Media"
    },
    {
        "keywords": ["telephone etiquette", "professional speaking", "greeting", "escalation"],
        "domains": ["bpo", "customer", "support"],
        "video_url": "https://www.youtube.com/watch?v=5CgAZZGd1uQ",
        "embed_url": "https://www.youtube.com/embed/5CgAZZGd1uQ",
        "title": "Top 3 Telephone Etiquette Tips for Success",
        "duration_str": "06:40 HD",
        "channel": "Business Training Media"
    },

    # --- PROJECT MANAGEMENT, PRODUCTIVITY & DIGITAL TOOLS ---
    {
        "keywords": ["project management", "project", "pmp", "agile", "scrum", "kanban", "timeline", "scope", "digital first", "tools"],
        "domains": ["project", "management", "digital", "tools", "agile", "scrum"],
        "video_url": "https://www.youtube.com/watch?v=BSCC4Bnkbfo",
        "embed_url": "https://www.youtube.com/embed/BSCC4Bnkbfo",
        "title": "Project Management Full Course | From Basics to Masterclass",
        "duration_str": "45:30 HD",
        "channel": "Simplilearn"
    },
    {
        "keywords": ["framework", "methodology", "waterfall", "planning", "deliverable"],
        "domains": ["project", "management", "digital"],
        "video_url": "https://www.youtube.com/watch?v=Wg7575ZVxxg",
        "embed_url": "https://www.youtube.com/embed/Wg7575ZVxxg",
        "title": "Project Management Tutorial & Methodologies Overview",
        "duration_str": "28:15 HD",
        "channel": "Simplilearn"
    },
    {
        "keywords": ["foundations", "lifecycle", "initiation", "closure", "stakeholder"],
        "domains": ["project", "management", "leadership"],
        "video_url": "https://www.youtube.com/watch?v=1Ems81iaS6Q",
        "embed_url": "https://www.youtube.com/embed/1Ems81iaS6Q",
        "title": "Foundations Of Project Management & Tool Ecosystems",
        "duration_str": "22:40 HD",
        "channel": "Simplilearn"
    },
    {
        "keywords": ["time management", "pomodoro", "productivity", "prioritization", "efficiency"],
        "domains": ["time", "productivity", "management", "efficiency"],
        "video_url": "https://www.youtube.com/watch?v=iDbdXTMnOmE",
        "embed_url": "https://www.youtube.com/embed/iDbdXTMnOmE",
        "title": "How to Manage Your Time More Effectively (According to Science)",
        "duration_str": "11:20 HD",
        "channel": "TED-Ed"
    },
    {
        "keywords": ["eisenhower", "matrix", "urgent", "important", "delegation"],
        "domains": ["time", "productivity", "management", "leadership"],
        "video_url": "https://www.youtube.com/watch?v=tT89OZ7TNwc",
        "embed_url": "https://www.youtube.com/embed/tT89OZ7TNwc",
        "title": "The Eisenhower Matrix: Explained in 2 Minutes",
        "duration_str": "02:40 HD",
        "channel": "Sprouts / Eisenhower"
    },
    {
        "keywords": ["procrastination", "focus", "habits", "distraction", "deep work"],
        "domains": ["time", "productivity", "focus"],
        "video_url": "https://www.youtube.com/watch?v=arj7oStGLkU",
        "embed_url": "https://www.youtube.com/embed/arj7oStGLkU",
        "title": "Inside the Mind of a Master Procrastinator",
        "duration_str": "14:05 HD",
        "channel": "Tim Urban | TED"
    },

    # --- WEB DEVELOPMENT & PROGRAMMING ---
    {
        "keywords": ["python", "programming", "code", "backend", "django", "function", "variable"],
        "domains": ["python", "programming", "software", "backend", "it"],
        "video_url": "https://www.youtube.com/watch?v=_uQrJ0TkZlc",
        "embed_url": "https://www.youtube.com/embed/_uQrJ0TkZlc",
        "title": "Python Full Course for Beginners [Tutorial]",
        "duration_str": "42:00 HD",
        "channel": "Programming with Mosh"
    },
    {
        "keywords": ["javascript", "frontend", "html", "css", "web development", "dom"],
        "domains": ["web", "javascript", "frontend", "programming", "software"],
        "video_url": "https://www.youtube.com/watch?v=W6NZfCO5SIk",
        "embed_url": "https://www.youtube.com/embed/W6NZfCO5SIk",
        "title": "JavaScript Course for Beginners: First Steps to Web Dev",
        "duration_str": "48:00 HD",
        "channel": "Programming with Mosh"
    },
    {
        "keywords": ["sql", "database", "query", "select", "table", "relational", "postgres"],
        "domains": ["sql", "database", "backend", "software", "it"],
        "video_url": "https://www.youtube.com/watch?v=HXV3zeQKqGY",
        "embed_url": "https://www.youtube.com/embed/HXV3zeQKqGY",
        "title": "SQL Tutorial - Full Database Course for Beginners",
        "duration_str": "45:00 HD",
        "channel": "freeCodeCamp.org"
    },
    {
        "keywords": ["git", "github", "version control", "open source", "repository"],
        "domains": ["git", "programming", "software", "web", "it"],
        "video_url": "https://www.youtube.com/watch?v=V74l_zS1x8E",
        "embed_url": "https://www.youtube.com/embed/V74l_zS1x8E",
        "title": "Contributing to Open Source & Version Control Fundamentals",
        "duration_str": "15:30 HD",
        "channel": "The Odin Project"
    },
    {
        "keywords": ["react", "frontend", "component", "jsx", "state", "props", "hook"],
        "domains": ["react", "web", "frontend", "javascript", "software"],
        "video_url": "https://www.youtube.com/watch?v=bMknfKXIFA8",
        "embed_url": "https://www.youtube.com/embed/bMknfKXIFA8",
        "title": "React Course - Beginner's Tutorial for React JavaScript Library",
        "duration_str": "45:00 HD",
        "channel": "freeCodeCamp.org"
    },

    # --- ARTIFICIAL INTELLIGENCE, MACHINE LEARNING & PROMPT ENGINEERING ---
    {
        "keywords": ["machine learning", "ai", "artificial intelligence", "supervised", "dataset", "algorithm", "model", "orientation"],
        "domains": ["ai", "machine learning", "python", "data", "software"],
        "video_url": "https://www.youtube.com/watch?v=Gv9_4yMHFhI",
        "embed_url": "https://www.youtube.com/embed/Gv9_4yMHFhI",
        "title": "A Gentle Introduction to Machine Learning Fundamentals",
        "duration_str": "15:30 HD",
        "channel": "StatQuest with Josh Starmer"
    },
    {
        "keywords": ["prompt engineering", "prompt", "chatgpt", "llm", "few-shot", "prompt formula", "ai prompting"],
        "domains": ["ai", "prompt engineering", "chatgpt", "llm", "machine learning"],
        "video_url": "https://www.youtube.com/watch?v=jC4v5AS4RIM",
        "embed_url": "https://www.youtube.com/embed/jC4v5AS4RIM",
        "title": "Master the Perfect ChatGPT Prompt Formula & AI Prompting",
        "duration_str": "08:45 HD",
        "channel": "Jeff Su"
    },
    {
        "keywords": ["neural network", "deep learning", "gradient descent", "backpropagation", "weights", "perceptron"],
        "domains": ["ai", "machine learning", "deep learning", "neural"],
        "video_url": "https://www.youtube.com/watch?v=aircAruvnKk",
        "embed_url": "https://www.youtube.com/embed/aircAruvnKk",
        "title": "Neural Networks & Deep Learning Explained",
        "duration_str": "19:15 HD",
        "channel": "3Blue1Brown"
    },
    {
        "keywords": ["what is ai", "artificial intelligence basics", "intro to ai", "ai fundamentals"],
        "domains": ["ai", "machine learning"],
        "video_url": "https://www.youtube.com/watch?v=ad79nYk2keg",
        "embed_url": "https://www.youtube.com/embed/ad79nYk2keg",
        "title": "What Is AI? | Artificial Intelligence in 5 Mins",
        "duration_str": "05:25 HD",
        "channel": "Simplilearn"
    },

    # --- CLOUD COMPUTING & LINUX ADMINISTRATION ---
    {
        "keywords": ["linux", "terminal", "bash", "command line", "sysadmin", "server"],
        "domains": ["linux", "cloud", "devops", "software", "it"],
        "video_url": "https://www.youtube.com/watch?v=ZtqBQ68cfJc",
        "embed_url": "https://www.youtube.com/embed/ZtqBQ68cfJc",
        "title": "The 50 Most Popular Linux & Terminal Commands - Full Course",
        "duration_str": "45:00 HD",
        "channel": "freeCodeCamp.org"
    },
    {
        "keywords": ["aws", "cloud", "s3", "ec2", "iam", "cloud practitioner"],
        "domains": ["cloud", "aws", "devops", "it"],
        "video_url": "https://www.youtube.com/watch?v=SOTamWNgDKc",
        "embed_url": "https://www.youtube.com/embed/SOTamWNgDKc",
        "title": "AWS Certified Cloud Practitioner Certification Course",
        "duration_str": "50:00 HD",
        "channel": "freeCodeCamp.org"
    },
    {
        "keywords": ["networking", "network", "tcp/ip", "subnet", "dns", "router", "switch", "troubleshoot"],
        "domains": ["network", "cloud", "it", "devops"],
        "video_url": "https://www.youtube.com/watch?v=qiQR5rTSshw",
        "embed_url": "https://www.youtube.com/embed/qiQR5rTSshw",
        "title": "Computer Networking Course - Network Engineering Fundamentals",
        "duration_str": "40:00 HD",
        "channel": "freeCodeCamp.org"
    },

    # --- DATA ANALYTICS & PYTHON APPLIED ---
    {
        "keywords": ["data analysis", "data analytics", "pandas", "numpy", "dataframe", "matplotlib", "seaborn", "analytics", "automation"],
        "domains": ["data", "analytics", "python", "software"],
        "video_url": "https://www.youtube.com/watch?v=r-uOLxNrNk8",
        "embed_url": "https://www.youtube.com/embed/r-uOLxNrNk8",
        "title": "Data Analysis with Python - Full Course for Beginners",
        "duration_str": "38:00 HD",
        "channel": "freeCodeCamp.org"
    },
    {
        "keywords": ["python tutorial", "python basics", "python exercises", "functions", "loops"],
        "domains": ["python", "programming", "software"],
        "video_url": "https://www.youtube.com/watch?v=rfscVS0vtbw",
        "embed_url": "https://www.youtube.com/embed/rfscVS0vtbw",
        "title": "Learn Python - Full Practical Course for Beginners",
        "duration_str": "35:00 HD",
        "channel": "freeCodeCamp.org"
    },

    # --- CULINARY ARTS & COMMERCIAL COOKERY ---
    {
        "keywords": ["knife", "cutting", "chop", "julienne", "dice", "culinary", "cookery", "kitchen"],
        "domains": ["cookery", "culinary", "kitchen", "cooking", "food"],
        "video_url": "https://www.youtube.com/watch?v=G-Fg7l7G1zw",
        "embed_url": "https://www.youtube.com/embed/G-Fg7l7G1zw",
        "title": "Basic Knife Skills & Commercial Kitchen Cutting Techniques",
        "duration_str": "08:35 HD",
        "channel": "Tasty / Gordon Ramsay"
    },

    # --- CAREGIVING, FIRST AID & HEALTHCARE ---
    {
        "keywords": ["cpr", "bls", "first aid", "choking", "cardiopulmonary", "emergency", "health"],
        "domains": ["caregiving", "health", "first aid", "emergency", "nursing"],
        "video_url": "https://www.youtube.com/watch?v=-NodDRTsV88",
        "embed_url": "https://www.youtube.com/embed/ -NodDRTsV88".replace(" ", ""),
        "title": "Adult CPR & Basic Life Support (BLS) Hands-on Protocol",
        "duration_str": "05:40 HD",
        "channel": "Cincinnati Children's / Red Cross"
    },
    {
        "keywords": ["patient transfer", "lifting", "wheelchair", "bed", "mobility", "caregiver"],
        "domains": ["caregiving", "nursing", "health"],
        "video_url": "https://www.youtube.com/watch?v=lSp4qnTAuQQ",
        "embed_url": "https://www.youtube.com/embed/lSp4qnTAuQQ",
        "title": "Lifting and Transferring of Patients Safe Technique",
        "duration_str": "11:20 HD",
        "channel": "Nursing with Love"
    },
    {
        "keywords": ["stroke", "bed to wheelchair", "ambulation", "rehabilitation"],
        "domains": ["caregiving", "health", "medical"],
        "video_url": "https://www.youtube.com/watch?v=re6TnoGpXdM",
        "embed_url": "https://www.youtube.com/embed/re6TnoGpXdM",
        "title": "Safe Patient Transfer: Bed to Wheelchair Highlights",
        "duration_str": "04:50 HD",
        "channel": "American Heart Association"
    },

    # --- BOOKKEEPING & FINANCIAL ACCOUNTING ---
    {
        "keywords": ["bookkeeping", "ledger", "journal", "debit", "credit", "balance sheet", "accounting", "finance"],
        "domains": ["accounting", "bookkeeping", "finance", "tax", "office"],
        "video_url": "https://www.youtube.com/watch?v=yYX4bvQSqbo",
        "embed_url": "https://www.youtube.com/embed/yYX4bvQSqbo",
        "title": "ACCOUNTING BASICS: A Complete Guide to Everything",
        "duration_str": "14:30 HD",
        "channel": "Accounting Stuff"
    }
]


def _match_token_or_substring(pattern, text):
    """
    Checks if a domain or keyword matches within text. Uses word boundaries
    for all single-word tokens to prevent false positives like 'auto' in 'automation',
    'engine' in 'engineering', or 'car' in 'career'.
    """
    if " " in pattern:
        return pattern in text
    return bool(re.search(rf"\b{re.escape(pattern)}\b", text))


def get_aligned_video_for_topic(topic, module_title="", lesson_title="", used_urls=None, lesson_index=0, module_num=1):
    """
    Intelligently finds and resolves topic-aligned vocational/educational YouTube videos.
    Matches against curated high-authority trade tutorials or selects a guaranteed-working
    foundational tutorial matching the general trade domain.
    Ensures that different modules within the same training program receive distinct,
    stage-appropriate videos (Module 1: Foundations, Module 2: Applied Operations, Module 4: Troubleshooting).
    """
    used_set = set(used_urls or [])
    full_context = f"{topic} {module_title} {lesson_title}".lower()

    # Stage detection based on module number or lesson naming
    mod_str = f"{module_title} {lesson_title}".lower()
    is_foundation = (module_num == 1) or any(k in mod_str for k in ["orient", "intro", "foundation", "basic", "overview", "principle", "1.1"])
    is_operations = (module_num == 2) or any(k in mod_str for k in ["operat", "tool", "execut", "hands-on", "workflow", "step", "demonstrat", "2.1"])
    is_troubleshoot = (module_num >= 4) or any(k in mod_str for k in ["troubleshoot", "diagnostic", "problem", "root cause", "advanced", "triage", "quality", "audit", "4.1"])

    # 1. Rank matches from curated collection
    topic_lower = topic.lower()
    ranked = []
    for item in CURATED_VOCATIONAL_VIDEOS:
        topic_domain_match = any(_match_token_or_substring(d, topic_lower) for d in item["domains"])
        context_domain_match = any(_match_token_or_substring(d, full_context) for d in item["domains"])
        keyword_hits = sum(1 for kw in item["keywords"] if _match_token_or_substring(kw, full_context))

        # Base domain score: strong priority if video matches program topic
        if topic_domain_match:
            score = 25 + (keyword_hits * 3)
        elif context_domain_match:
            score = 12 + (keyword_hits * 3)
        elif keyword_hits >= 2:
            score = keyword_hits * 3
        else:
            score = 0

        # Stage affinity bonus (only apply if video belongs to domain)
        if score > 0:
            item_title_lower = item["title"].lower()
            if is_foundation and any(w in item_title_lower for w in ["basics", "fundamentals", "overview", "full course", "intro", "works", "what is"]):
                score += 5
            elif is_operations and any(w in item_title_lower for w in ["tutorial", "step by step", "operations", "getting started", "practical", "how to", "replace", "wiring", "prompt"]):
                score += 5
            elif is_troubleshoot and any(w in item_title_lower for w in ["troubleshoot", "diagnostic", "multimeter", "scanner", "test", "advanced", "triaging", "deep learning", "neural", "rules", "inspection"]):
                score += 5

        # Heavily deprioritize already used video URLs in this training program so each module gets a unique video!
        if item["video_url"] in used_set or item["embed_url"] in used_set:
            score -= 100

        if score > 0:
            ranked.append((score, item))

    ranked.sort(key=lambda x: x[0], reverse=True)

    search_query = f"{topic} {lesson_title or module_title} tutorial".strip()
    encoded_query = urllib.parse.quote_plus(search_query)

    if ranked and ranked[0][0] > 0:
        top_match = ranked[0][1]
        alternatives = [
            {
                "title": r[1]["title"],
                "video_url": r[1]["video_url"],
                "embed_url": r[1]["embed_url"],
                "duration_str": r[1]["duration_str"],
                "channel": r[1]["channel"]
            }
            for r in ranked[1:4]
        ]
        alternatives.append({
            "title": f"Search '{topic}' tutorials on YouTube",
            "video_url": f"https://www.youtube.com/results?search_query={encoded_query}",
            "embed_url": top_match["embed_url"],
            "duration_str": "Various",
            "channel": "YouTube Search"
        })

        return {
            "source": "curated",
            "title": top_match["title"],
            "video_url": top_match["video_url"],
            "embed_url": top_match["embed_url"],
            "duration_str": top_match["duration_str"],
            "channel": top_match.get("channel", "Technical Vocational Institute"),
            "search_query": search_query,
            "alternatives": alternatives
        }

    # 2. Domain-Matched Stage-Aware Guaranteed Working Fallbacks
    # Each domain has a distinct list: [Stage 1: Foundations, Stage 2: Operations, Stage 4: Troubleshooting]
    if any(k in full_context for k in ["prompt", "machine learning", "artificial intelligence", "ai ", "deep learning", "neural", "llm", "chatgpt"]):
        domain_fallbacks = [
            {"title": "A Gentle Introduction to Machine Learning Fundamentals", "video_url": "https://www.youtube.com/watch?v=Gv9_4yMHFhI", "embed_url": "https://www.youtube.com/embed/Gv9_4yMHFhI", "duration_str": "15:30 HD", "channel": "StatQuest with Josh Starmer"},
            {"title": "Master the Perfect ChatGPT Prompt Formula & AI Prompting", "video_url": "https://www.youtube.com/watch?v=jC4v5AS4RIM", "embed_url": "https://www.youtube.com/embed/jC4v5AS4RIM", "duration_str": "08:45 HD", "channel": "Jeff Su"},
            {"title": "Neural Networks & Deep Learning Explained", "video_url": "https://www.youtube.com/watch?v=aircAruvnKk", "embed_url": "https://www.youtube.com/embed/aircAruvnKk", "duration_str": "19:15 HD", "channel": "3Blue1Brown"}
        ]
    elif any(k in full_context for k in ["program", "code", "python", "software", "backend"]):
        domain_fallbacks = [
            {"title": "Python Full Course for Beginners [Tutorial]", "video_url": "https://www.youtube.com/watch?v=_uQrJ0TkZlc", "embed_url": "https://www.youtube.com/embed/_uQrJ0TkZlc", "duration_str": "42:00 HD", "channel": "Programming with Mosh"},
            {"title": "Learn Python - Full Practical Course for Beginners", "video_url": "https://www.youtube.com/watch?v=rfscVS0vtbw", "embed_url": "https://www.youtube.com/embed/rfscVS0vtbw", "duration_str": "35:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "Contributing to Open Source & Version Control Fundamentals", "video_url": "https://www.youtube.com/watch?v=V74l_zS1x8E", "embed_url": "https://www.youtube.com/embed/V74l_zS1x8E", "duration_str": "15:30 HD", "channel": "The Odin Project"}
        ]
    elif any(k in full_context for k in ["react", "javascript", "frontend", "web", "html", "css"]):
        domain_fallbacks = [
            {"title": "JavaScript Course for Beginners: First Steps to Web Dev", "video_url": "https://www.youtube.com/watch?v=W6NZfCO5SIk", "embed_url": "https://www.youtube.com/embed/W6NZfCO5SIk", "duration_str": "48:00 HD", "channel": "Programming with Mosh"},
            {"title": "React Course - Beginner's Tutorial for React JavaScript Library", "video_url": "https://www.youtube.com/watch?v=bMknfKXIFA8", "embed_url": "https://www.youtube.com/embed/bMknfKXIFA8", "duration_str": "45:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "Contributing to Open Source & Version Control Fundamentals", "video_url": "https://www.youtube.com/watch?v=V74l_zS1x8E", "embed_url": "https://www.youtube.com/embed/V74l_zS1x8E", "duration_str": "15:30 HD", "channel": "The Odin Project"}
        ]
    elif any(k in full_context for k in ["cloud", "aws", "linux", "network", "devops"]):
        domain_fallbacks = [
            {"title": "The 50 Most Popular Linux & Terminal Commands - Full Course", "video_url": "https://www.youtube.com/watch?v=ZtqBQ68cfJc", "embed_url": "https://www.youtube.com/embed/ZtqBQ68cfJc", "duration_str": "45:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "AWS Certified Cloud Practitioner Certification Course", "video_url": "https://www.youtube.com/watch?v=SOTamWNgDKc", "embed_url": "https://www.youtube.com/embed/SOTamWNgDKc", "duration_str": "50:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "Computer Networking Course - Network Engineering Fundamentals", "video_url": "https://www.youtube.com/watch?v=qiQR5rTSshw", "embed_url": "https://www.youtube.com/embed/qiQR5rTSshw", "duration_str": "40:00 HD", "channel": "freeCodeCamp.org"}
        ]
    elif any(k in full_context for k in ["data", "sql", "analytics", "database"]):
        domain_fallbacks = [
            {"title": "SQL Tutorial - Full Database Course for Beginners", "video_url": "https://www.youtube.com/watch?v=HXV3zeQKqGY", "embed_url": "https://www.youtube.com/embed/HXV3zeQKqGY", "duration_str": "45:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "Data Analysis with Python - Full Course for Beginners", "video_url": "https://www.youtube.com/watch?v=r-uOLxNrNk8", "embed_url": "https://www.youtube.com/embed/r-uOLxNrNk8", "duration_str": "38:00 HD", "channel": "freeCodeCamp.org"},
            {"title": "Python Full Course for Beginners [Tutorial]", "video_url": "https://www.youtube.com/watch?v=_uQrJ0TkZlc", "embed_url": "https://www.youtube.com/embed/_uQrJ0TkZlc", "duration_str": "42:00 HD", "channel": "Programming with Mosh"}
        ]
    elif any(k in full_context for k in ["weld", "metal", "steel", "smaw"]):
        domain_fallbacks = [
            {"title": "Stick Welding Basics: Full Tutorial (SMAW Essentials)", "video_url": "https://www.youtube.com/watch?v=Dybrlx-BTXk", "embed_url": "https://www.youtube.com/embed/Dybrlx-BTXk", "duration_str": "16:45 HD", "channel": "TimWelds"},
            {"title": "Stick Welding 101: Getting Started With SMAW", "video_url": "https://www.youtube.com/watch?v=4MKuUICV6-c", "embed_url": "https://www.youtube.com/embed/4MKuUICV6-c", "duration_str": "18:20 HD", "channel": "Weld.com"},
            {"title": "Intro to SMAW: Stick Welding for Beginners", "video_url": "https://www.youtube.com/watch?v=3UEn4FjeOx4", "embed_url": "https://www.youtube.com/embed/3UEn4FjeOx4", "duration_str": "12:35 HD", "channel": "WeldFever"}
        ]
    elif any(k in full_context for k in ["auto", "car", "engine", "mechanic", "motor", "vehicle"]):
        domain_fallbacks = [
            {"title": "How a Car Engine Works (3D Animated Breakdown)", "video_url": "https://www.youtube.com/watch?v=ZQvfHyfgBtA", "embed_url": "https://www.youtube.com/embed/ZQvfHyfgBtA", "duration_str": "11:50 HD", "channel": "Animagraffs"},
            {"title": "How to Replace Brake Pads and Rotors (COMPLETE Guide)", "video_url": "https://www.youtube.com/watch?v=6RQ9UabOIPg", "embed_url": "https://www.youtube.com/embed/6RQ9UabOIPg", "duration_str": "21:10 HD", "channel": "ChrisFix"},
            {"title": "How to Use an OBD2 Scanner | Step by Step for Beginners", "video_url": "https://www.youtube.com/watch?v=lpenIb9Yfn4", "embed_url": "https://www.youtube.com/embed/lpenIb9Yfn4", "duration_str": "14:20 HD", "channel": "WeeklyTools"}
        ]
    elif any(k in full_context for k in ["electric", "wire", "power", "circuit", "eim"]):
        domain_fallbacks = [
            {"title": "How ELECTRICITY Works - Working Principle & Fundamentals", "video_url": "https://www.youtube.com/watch?v=mc979OhitAg", "embed_url": "https://www.youtube.com/embed/mc979OhitAg", "duration_str": "10:20 HD", "channel": "The Engineering Mindset"},
            {"title": "House Wiring Tutorial: Basic Electrical Installation", "video_url": "https://www.youtube.com/watch?v=y4cSaZs7tU8", "embed_url": "https://www.youtube.com/embed/y4cSaZs7tU8", "duration_str": "24:15 HD", "channel": "Local Electrician"},
            {"title": "Home Basic Electrical Wiring Fundamentals", "video_url": "https://www.youtube.com/watch?v=LPuCicN4ahc", "embed_url": "https://www.youtube.com/embed/LPuCicN4ahc", "duration_str": "15:40 HD", "channel": "Japegz TV"}
        ]
    elif any(k in full_context for k in ["aircon", "hvac", "refrigerat", "cooling", "rac"]):
        domain_fallbacks = [
            {"title": "Air Conditioning System Basics - HVACR How Does It Work", "video_url": "https://www.youtube.com/watch?v=SfuSzBja8QA", "embed_url": "https://www.youtube.com/embed/SfuSzBja8QA", "duration_str": "16:30 HD", "channel": "The Engineering Mindset"},
            {"title": "Basic Refrigeration Cycle - How It Works", "video_url": "https://www.youtube.com/watch?v=Uv3GfEQhtPE", "embed_url": "https://www.youtube.com/embed/Uv3GfEQhtPE", "duration_str": "11:45 HD", "channel": "The Engineering Mindset"},
            {"title": "How Solar Photovoltaic (PV) Panels Generate Electricity", "video_url": "https://www.youtube.com/watch?v=Yxt72aDjFgY", "embed_url": "https://www.youtube.com/embed/Yxt72aDjFgY", "duration_str": "12:40 HD", "channel": "The Engineering Mindset"}
        ]
    elif any(k in full_context for k in ["plumb", "pipe", "sanitary", "fitting"]):
        domain_fallbacks = [
            {"title": "How to Transition Plumbing Pipes (PEX, Copper, PVC Guide)", "video_url": "https://www.youtube.com/watch?v=dD-Qem26i6s", "embed_url": "https://www.youtube.com/embed/dD-Qem26i6s", "duration_str": "14:50 HD", "channel": "Got2Learn"},
            {"title": "How to Solder Copper Pipe The Correct Way", "video_url": "https://www.youtube.com/watch?v=JyVbjDsric4", "embed_url": "https://www.youtube.com/embed/JyVbjDsric4", "duration_str": "11:20 HD", "channel": "Got2Learn"},
            {"title": "How to Transition Plumbing Pipes (PEX, Copper, PVC Guide)", "video_url": "https://www.youtube.com/watch?v=dD-Qem26i6s", "embed_url": "https://www.youtube.com/embed/dD-Qem26i6s", "duration_str": "14:50 HD", "channel": "Got2Learn"}
        ]
    elif any(k in full_context for k in ["health", "care", "nurse", "aid", "first aid"]):
        domain_fallbacks = [
            {"title": "10 Fundamental Rules For Workplace Safety", "video_url": "https://www.youtube.com/watch?v=BVCmz9KnwWk", "embed_url": "https://www.youtube.com/embed/BVCmz9KnwWk", "duration_str": "10:30 HD", "channel": "ESS INFO Safety"},
            {"title": "Adult CPR & Basic Life Support (BLS) Hands-on Protocol", "video_url": "https://www.youtube.com/watch?v=-NodDRTsV88", "embed_url": "https://www.youtube.com/embed/-NodDRTsV88", "duration_str": "05:40 HD", "channel": "Red Cross"},
            {"title": "Lifting and Transferring of Patients Safe Technique", "video_url": "https://www.youtube.com/watch?v=lSp4qnTAuQQ", "embed_url": "https://www.youtube.com/embed/lSp4qnTAuQQ", "duration_str": "11:20 HD", "channel": "Nursing with Love"}
        ]
    elif any(k in full_context for k in ["bpo", "customer", "call center", "support"]):
        domain_fallbacks = [
            {"title": "10 Telephone Customer Service Tips | Telephone Etiquette", "video_url": "https://www.youtube.com/watch?v=MGKhCFIUwq8", "embed_url": "https://www.youtube.com/embed/MGKhCFIUwq8", "duration_str": "09:50 HD", "channel": "Business Training Media"},
            {"title": "Top 3 Telephone Etiquette Tips for Success", "video_url": "https://www.youtube.com/watch?v=5CgAZZGd1uQ", "embed_url": "https://www.youtube.com/embed/5CgAZZGd1uQ", "duration_str": "06:40 HD", "channel": "Business Training Media"},
            {"title": "10 Fundamental Rules For Workplace Safety", "video_url": "https://www.youtube.com/watch?v=BVCmz9KnwWk", "embed_url": "https://www.youtube.com/embed/BVCmz9KnwWk", "duration_str": "10:30 HD", "channel": "ESS INFO Safety"}
        ]
    else:
        domain_fallbacks = [
            {"title": "Project Management Full Course | From Basics to Masterclass", "video_url": "https://www.youtube.com/watch?v=BSCC4Bnkbfo", "embed_url": "https://www.youtube.com/embed/BSCC4Bnkbfo", "duration_str": "45:30 HD", "channel": "Simplilearn"},
            {"title": "Project Management Tutorial & Methodologies Overview", "video_url": "https://www.youtube.com/watch?v=Wg7575ZVxxg", "embed_url": "https://www.youtube.com/embed/Wg7575ZVxxg", "duration_str": "28:15 HD", "channel": "Simplilearn"},
            {"title": "10 Fundamental Rules For Workplace Safety", "video_url": "https://www.youtube.com/watch?v=BVCmz9KnwWk", "embed_url": "https://www.youtube.com/embed/BVCmz9KnwWk", "duration_str": "10:30 HD", "channel": "ESS INFO Safety"}
        ]

    # Select the stage-appropriate fallback, prioritizing unused videos
    target_idx = 0 if is_foundation else (1 if is_operations else 2)
    target_idx = min(target_idx, len(domain_fallbacks) - 1)

    selected_vid = None
    if domain_fallbacks[target_idx]["video_url"] not in used_set:
        selected_vid = domain_fallbacks[target_idx]
    else:
        # Pick first unused in the domain list
        for cand in domain_fallbacks:
            if cand["video_url"] not in used_set:
                selected_vid = cand
                break
        if not selected_vid:
            selected_vid = domain_fallbacks[target_idx]

    return {
        "source": "curated_fallback",
        "title": selected_vid["title"],
        "video_url": selected_vid["video_url"],
        "embed_url": selected_vid["embed_url"],
        "duration_str": selected_vid["duration_str"],
        "channel": selected_vid["channel"],
        "search_query": search_query,
        "alternatives": [
            {
                "title": f"YouTube Video Search: {topic}",
                "video_url": f"https://www.youtube.com/results?search_query={encoded_query}",
                "embed_url": selected_vid["embed_url"],
                "duration_str": "Various",
                "channel": "YouTube"
            }
        ]
    }


def _build_rich_reading_content(headline_text, summary_text):
    """
    Constructs a thorough, multi-paragraph 3-phase technical guide for reading lessons.
    """
    clean_lead = summary_text.strip() if summary_text and summary_text.strip() else f"Comprehensive practical guide and operational standards for {headline_text}."
    return f"""### 1. Operational Overview & Competency Requirements
{clean_lead}

### 2. Standard Operating Procedures (SOP) & Technical Execution
To achieve industry-standard proficiency in **{headline_text}**, technicians and candidates must follow this systematic execution workflow:
- **Phase 1: Pre-Operation Preparation & Tool Calibration:** Conduct a thorough 360-degree workspace hazard check, inspect specialized safety gear and PPE, and ensure all tools are calibrated according to manufacturer specifications.
- **Phase 2: Procedural Execution & Safe Handling:** Follow established Philippine vocational standards step-by-step. Maintain consistent operational rhythm, monitor tolerances, and document procedural parameters.
- **Phase 3: Quality Verification & Tolerance Audits:** Perform visual and dimensional inspection of the output. Verify all specifications against industry tolerances before sign-off.

### 3. Safety Compliance & Common Error Prevention
- Always adhere to mandatory occupational safety and health (OSH) directives during active handling.
- If equipment resistance, abnormal heat, or calibration drift occurs, immediately engage emergency stop protocols and report to the supervising trainer.
- Maintain a clean, organized work area and complete all required operational logs prior to lesson sign-off."""


# =========================================================================
# AI FEATURE 3: PESO TRAINING PROGRAM & CURRICULUM GENERATOR
# =========================================================================

def _clean_program_title(t):
    if not t:
        return ""
    raw = str(t).strip()
    raw = re.sub(r'(?i)\bnc[\s-]*ii\b', 'NC II', raw)
    raw = re.sub(r'(?i)\bnc[\s-]*i\b', 'NC I', raw)
    raw = re.sub(r'(?i)\bnc[\s-]*iii\b', 'NC III', raw)
    raw = re.sub(r'(?i)\bnc[\s-]*iv\b', 'NC IV', raw)
    raw = re.sub(r'(?i)\bsmaw\b', 'SMAW', raw)
    raw = re.sub(r'(?i)\beim\b', 'EIM', raw)
    raw = re.sub(r'(?i)\brac\b', 'RAC', raw)
    raw = re.sub(r'(?i)\bhvac\b', 'HVAC', raw)
    raw = re.sub(r'(?i)\bobd\b', 'OBD', raw)
    raw = re.sub(r'(?i)\bcosh\b', 'COSH', raw)
    raw = re.sub(r'(?i)\bbosh\b', 'BOSH', raw)
    if not any(k in raw.lower() for k in ["masterclass", "training program", "course", "bootcamp"]):
        raw = f"{raw} Professional Masterclass"
    return raw.strip()


def generate_training_program_ai(
    topic,
    target_skills_str="",
    duration_hours=40,
    required_seat_time_mins=60,
    num_modules=10,
    lessons_per_module=10,
    attach_videos=True,
    training_needs=""
):
    """
    Automatically creates a comprehensive, vocational LMS training program curriculum
    with up to 10 structured modules, 10+ milestone lessons per module (100 lessons total),
    topic-aligned video tutorial attachments, and certification assessment questions.
    """
    clean_topic = _clean_program_title(topic)
    num_modules = max(3, min(int(num_modules or 10), 12))
    lessons_per_module = max(2, min(int(lessons_per_module or 10), 15))

    system_prompt = (
        "You are an expert vocational curriculum developer for TESDA, DOLE, and the Public Employment Service Office (PESO) in the Philippines. "
        "You create high-standard, comprehensive, and practical vocational, technical, and professional training programs. "
        "Output strictly valid JSON with no markdown wrapping."
    )

    prompt = f"""
Generate a comprehensive vocational masterclass syllabus outline for: '{clean_topic}'.
Target competencies: {target_skills_str or topic}.
Display Duration: {duration_hours} Hours.
Minimum Required Seat-Time: {required_seat_time_mins} Minutes.

Specific training needs/context from the PESO coordinator:
{training_needs or 'Not specified — design a comprehensive, industry-standard scope for this role.'}
Use this context to prioritize the module topics, practical examples, equipment, worksite scenarios, and assessment questions. If the context mentions specific roles, tools, or tasks, weave them directly into the lessons.

Curriculum Structure Requirements:
1. Provide exactly {num_modules} progressive modules covering the full scope from workshop safety and fundamentals to advanced execution, diagnostics, and final inspection.
2. In each module, provide an array of exactly {lessons_per_module} concise lesson titles as strings (e.g. "1.1 Workshop Safety Protocols", "1.2 PPE & Equipment Orientation", ...).
3. Provide 8 practical multiple-choice certification exam questions.

Output strictly valid JSON with this exact structure:
{{
  "title": "{clean_topic}",
  "provider": "TESDA & PESO Training Center",
  "duration": "{duration_hours} Hours",
  "required_training_minutes": {required_seat_time_mins},
  "location": "PESO Training Center / Online LMS",
  "description": "Comprehensive {num_modules}-module masterclass covering end-to-end vocational competencies in {topic}.",
  "skills": ["{clean_topic}", "Occupational Safety", "Quality Assurance", "Equipment Maintenance", "Applied Technical Execution"],
  "curriculum": {{
    "course_overview": "Detailed overview of the {num_modules * lessons_per_module}-lesson vocational masterclass and industrial competency syllabus...",
    "modules": [
      {{
        "id": "mod-1",
        "title": "Module 1: Workshop Safety, Regulations & Equipment Orientation",
        "description": "Foundational occupational health and safety standards, hazard identification, and tool setup.",
        "lessons": [
          "1.1 Workshop Safety Protocols & PPE Requirements",
          "1.2 Hazardous Materials Handling & Waste Disposal",
          "1.3 Lifting Equipment & Pre-Operation Safety Checks",
          "1.4 Hand & Power Tools Standard Operating Procedures",
          "1.5 Fasteners, Gaskets, and Torque Specifications",
          "1.6 Precision Measuring Instruments & Calipers",
          "1.7 Service Manual Navigation & Technical Information",
          "1.8 Pre-Service Workpiece & Handover Inspection",
          "1.9 Fire Prevention & Emergency Response Protocols",
          "1.10 Workshop Safety Knowledge Checkpoint"
        ]
      }}
    ],
    "assessment": [
      {{
        "id": "q1",
        "question": "What is the critical first step before beginning technical work in {topic}?",
        "options": ["Perform safety hazard inspection and don required PPE", "Immediately begin operating machinery at maximum power", "Bypass calibration checks to expedite output", "Leave machinery unattended"],
        "correct": 0,
        "explanation": "Safety hazard inspection and PPE compliance are mandatory before starting operations."
      }}
    ]
  }}
}}
"""

    ai_data = call_gemini_api(prompt, system_instruction=system_prompt, json_mode=True, timeout=55)

    if ai_data and isinstance(ai_data, dict) and "curriculum" in ai_data:
        ai_data["ai_generated"] = True
        curr = ai_data.get("curriculum", {})

        raw_modules = curr.get("modules", [])
        hydrated_modules = []

        # Iterate and expand modules up to num_modules
        for m_idx in range(1, num_modules + 1):
            if m_idx <= len(raw_modules):
                raw_mod = raw_modules[m_idx - 1]
                mod_title = raw_mod.get("title") or f"Module {m_idx}: Core Technical Phase {m_idx} of {clean_topic}"
                mod_desc = raw_mod.get("description") or raw_mod.get("summary") or f"In-depth competency phase {m_idx} covering technical workflows for {clean_topic}."
                raw_lessons = raw_mod.get("lessons", [])
            else:
                mod_title = f"Module {m_idx}: Advanced Practical Application Phase {m_idx} — {clean_topic}"
                mod_desc = f"Comprehensive industrial execution, quality assurance, and standard operating procedures for Phase {m_idx}."
                raw_lessons = []

            mod_obj = {
                "id": f"mod-{m_idx}",
                "number": m_idx,
                "title": mod_title,
                "description": mod_desc,
                "summary": mod_desc,
                "lessons": []
            }

            # Expand lessons up to lessons_per_module
            for l_idx in range(1, lessons_per_module + 1):
                raw_les = raw_lessons[l_idx - 1] if l_idx <= len(raw_lessons) else None

                if isinstance(raw_les, str):
                    les_title = raw_les.strip()
                    les_summary = f"Practical competency guidelines and technical procedures for {les_title}."
                    # Alternate lesson types: Video for 1, 4, 7; Activity for last; Reading for rest
                    l_type = "video" if l_idx in [1, 4, 7] else ("activity" if l_idx == lessons_per_module else "reading")
                    les_content = {}
                elif isinstance(raw_les, dict):
                    les_title = raw_les.get("title") or f"{m_idx}.{l_idx} Competency Milestone {l_idx}"
                    les_summary = raw_les.get("summary") or raw_les.get("description") or f"Mastering practical execution for {les_title}."
                    l_type = raw_les.get("type") or ("video" if l_idx in [1, 4, 7] else ("activity" if l_idx == lessons_per_module else "reading"))
                    les_content = raw_les.get("content", {})
                else:
                    les_title = f"{m_idx}.{l_idx} Applied Technical Execution & Quality Gate {l_idx}"
                    les_summary = f"Step-by-step procedures, safety guidelines, and verification standards for {les_title}."
                    l_type = "video" if l_idx in [1, 4, 7] else ("activity" if l_idx == lessons_per_module else "reading")
                    les_content = {}

                les_id = f"les-{m_idx}-{l_idx}"
                duration_str = "25 mins" if l_type != "activity" else "15 mins"

                # Hydrate content by type
                if l_type == "video":
                    aligned_video = get_aligned_video_for_topic(clean_topic, mod_title, les_title) if attach_videos else {}
                    les_content = {
                        "heading": les_title,
                        "overview": les_summary,
                        "video_url": les_content.get("video_url") or aligned_video.get("video_url", ""),
                        "embed_url": les_content.get("embed_url") or aligned_video.get("embed_url", ""),
                        "video_placeholder_title": les_content.get("video_placeholder_title") or aligned_video.get("title", f"Video Masterclass: {les_title}"),
                        "video_duration_str": les_content.get("video_duration_str") or aligned_video.get("duration_str", "20:00 HD"),
                        "channel": aligned_video.get("channel", "Technical Vocational Institute"),
                        "key_points": [
                            f"Live demonstration of {les_title} following industrial standards.",
                            "Critical safety parameters, PPE verification, and equipment calibration.",
                            "Technique precision, tolerance maintenance, and error prevention.",
                            "Post-demonstration inspection and operational documentation."
                        ],
                        "reading_content": _build_rich_reading_content(les_title, les_summary)
                    }
                elif l_type == "activity":
                    les_content = {
                        "heading": f"{les_title} Checkpoint",
                        "overview": f"Validate your technical retention for Module {m_idx} before progressing.",
                        "quiz_question": f"In {clean_topic}, what is the mandatory protocol when an equipment tolerance drift or anomaly occurs?",
                        "options": [
                            "Halt execution, log the anomaly, inspect parameters, and recalibrate before resuming",
                            "Ignore the drift and force completion to meet deadlines",
                            "Discard all parts and restart without diagnostic evaluation",
                            "Bypass safety switches to restore operation"
                        ],
                        "correct_option_index": 0,
                        "explanation": "Halting operations, logging parameters, and recalibrating prevents cascade defects and guarantees safety compliance."
                    }
                else:  # reading
                    les_content = {
                        "heading": les_title,
                        "overview": les_summary,
                        "key_points": [
                            f"Master foundational principles and technical workflows for {les_title}.",
                            "Ensure 100% adherence to Philippine OSH standards and personal safety.",
                            "Execute standard operating procedures with precision tolerances.",
                            "Conduct self-inspection, quality verification, and maintenance logging."
                        ],
                        "reading_content": les_content.get("reading_content") if isinstance(les_content.get("reading_content"), str) and len(les_content.get("reading_content", "")) > 200 else _build_rich_reading_content(les_title, les_summary)
                    }

                mod_obj["lessons"].append({
                    "id": les_id,
                    "title": les_title,
                    "type": l_type,
                    "duration": duration_str,
                    "summary": les_summary,
                    "description": les_summary,
                    "content": les_content
                })

            hydrated_modules.append(mod_obj)

        curr["modules"] = hydrated_modules
        curr["total_lessons"] = sum(len(m["lessons"]) for m in hydrated_modules)

        # Ensure assessment questions exist
        if not curr.get("assessment") or len(curr.get("assessment", [])) < 5:
            curr["assessment"] = [
                {
                    "id": f"q{i}",
                    "question": f"In {clean_topic}, why is standard operating calibration critical during execution?",
                    "options": [
                        "It prevents operational defects, ensures safety compliance, and maintains precision tolerances",
                        "It is merely a formality and can be omitted during rush shifts",
                        "It is only required for newly purchased equipment",
                        "It replaces the need for personal protective gear"
                    ],
                    "correct": 0,
                    "explanation": "Routine calibration is mandatory to ensure tolerance accuracy and prevent industrial defects."
                }
                for i in range(1, 9)
            ]

        ai_data["curriculum"] = curr
        return ai_data

    # =========================================================================
    # ROBUST FALLBACK GENERATOR (When Gemini API is offline or rate-limited)
    # Generates complete, authentic 10-module x 10-lesson curriculum with videos
    # =========================================================================
    fallback_modules = []
    trade_phases = [
        ("Workshop Safety, OSH Directives & Equipment Orientation", "Mandatory occupational health, hazard identification, and tool setup procedures."),
        ("Fundamental Theory, Schematics & Industrial Architecture", "Core theoretical foundations, technical blueprints, and industry terminology."),
        ("Pre-Operational Checks, Tooling Calibration & Workspace Prep", "Systematic inspection checklists, diagnostic calibration, and readiness verification."),
        ("Standard Operating Procedures (SOP) & Technical Execution", "Hands-on execution workflows, standard tolerances, and precision handling."),
        ("Intermediate Operational Techniques & Workflow Precision", "Advanced procedural steps, continuous monitoring, and workflow optimization."),
        ("Advanced Methodologies, Machinery & Applied Operations", "Complex operational routines, specialized equipment handling, and productivity standards."),
        ("Systemic Diagnostics, Anomaly Isolation & Troubleshooting", "Root cause analysis, diagnostic trees, error containment, and calibration recovery."),
        ("Quality Assurance, Tolerance Verification & Compliance Audits", "Statutory compliance, dimensional verification, and quality sign-off protocols."),
        ("Enterprise Case Studies, Maintenance Drills & Best Practices", "Industry case study analysis, preventative maintenance, and incident prevention."),
        ("Capstone Practical Synthesis & Masterclass Assessment", "Comprehensive multi-domain review, practical synthesis, and certification defense.")
    ]

    for m_idx in range(1, num_modules + 1):
        phase_title, phase_desc = trade_phases[(m_idx - 1) % len(trade_phases)]
        mod_obj = {
            "id": f"mod-{m_idx}",
            "number": m_idx,
            "title": f"Module {m_idx}: {phase_title}",
            "description": f"{phase_desc} Tailored for {clean_topic}.",
            "summary": f"{phase_desc} Tailored for {clean_topic}.",
            "lessons": []
        }

        for l_idx in range(1, lessons_per_module + 1):
            les_id = f"les-{m_idx}-{l_idx}"
            l_type = "video" if l_idx in [1, 4, 7] else ("activity" if l_idx == lessons_per_module else "reading")
            duration_str = "25 mins" if l_type != "activity" else "15 mins"
            les_title = f"{m_idx}.{l_idx} Competency Phase {m_idx} — Technical Milestone {l_idx}"
            les_summary = f"Practical competency guidelines and technical procedures for {les_title} in {clean_topic}."

            if l_type == "video":
                aligned_video = get_aligned_video_for_topic(clean_topic, phase_title, les_title) if attach_videos else {}
                les_content = {
                    "heading": les_title,
                    "overview": les_summary,
                    "video_url": aligned_video.get("video_url", ""),
                    "embed_url": aligned_video.get("embed_url", ""),
                    "video_placeholder_title": aligned_video.get("title", f"Video Masterclass: {les_title}"),
                    "video_duration_str": aligned_video.get("duration_str", "20:00 HD"),
                    "channel": aligned_video.get("channel", "Technical Vocational Institute"),
                    "key_points": [
                        f"Live video demonstration of {les_title} following industrial standards.",
                        "Critical safety parameters, PPE verification, and equipment calibration.",
                        "Technique precision, tolerance maintenance, and error prevention.",
                        "Post-demonstration inspection and operational documentation."
                    ],
                    "reading_content": _build_rich_reading_content(les_title, les_summary)
                }
            elif l_type == "activity":
                les_content = {
                    "heading": f"{les_title} Checkpoint",
                    "overview": f"Validate your technical retention for Module {m_idx} before progressing.",
                    "quiz_question": f"In {clean_topic}, what is the mandatory protocol when an equipment tolerance drift or anomaly occurs?",
                    "options": [
                        "Halt execution, log the anomaly, inspect parameters, and recalibrate before resuming",
                        "Ignore the drift and force completion to meet deadlines",
                        "Discard all parts and restart without diagnostic evaluation",
                        "Bypass safety switches to restore operation"
                    ],
                    "correct_option_index": 0,
                    "explanation": "Halting operations, logging parameters, and recalibrating prevents cascade defects and guarantees safety compliance."
                }
            else:  # reading
                les_content = {
                    "heading": les_title,
                    "overview": les_summary,
                    "key_points": [
                        f"Master foundational principles and technical workflows for {les_title}.",
                        "Ensure 100% adherence to Philippine OSH standards and personal safety.",
                        "Execute standard operating procedures with precision tolerances.",
                        "Conduct self-inspection, quality verification, and maintenance logging."
                    ],
                    "reading_content": _build_rich_reading_content(les_title, les_summary)
                }

            mod_obj["lessons"].append({
                "id": les_id,
                "title": les_title,
                "type": l_type,
                "duration": duration_str,
                "summary": les_summary,
                "description": les_summary,
                "content": les_content
            })

        fallback_modules.append(mod_obj)

    fallback_curriculum = {
        "title": clean_topic,
        "provider": "TESDA & PESO Accredited Institute",
        "duration": f"{duration_hours} Hours",
        "required_training_minutes": required_seat_time_mins,
        "location": "PESO Digital Skills Hub / Online LMS",
        "description": f"Comprehensive {num_modules}-module competency-based training course in {clean_topic}, aligned with Philippine vocational standards and industry workforce demands.",
        "skills": [clean_topic, "Occupational Safety", "Industry Standards", "Quality Assurance", "Equipment Maintenance", "Applied Technical Execution"],
        "curriculum": {
            "course_overview": f"This program equips candidates with industry-standard capabilities in {clean_topic}. It covers safety fundamentals, technical execution, standard operating procedures, and comprehensive quality assurance across {num_modules} modules and {num_modules * lessons_per_module} milestone lessons.",
            "modules": fallback_modules,
            "total_lessons": num_modules * lessons_per_module,
            "assessment": [
                {
                    "id": f"q{i}",
                    "question": f"In {clean_topic}, what is the critical first step before beginning technical work?",
                    "options": [
                        "Perform a safety hazard inspection and don required Personal Protective Equipment (PPE)",
                        "Immediately begin operating at full capacity without checks",
                        "Bypass calibration checks to save time",
                        "Leave equipment unattended while warming up"
                    ],
                    "correct": 0,
                    "explanation": "Safety inspection and PPE compliance are mandatory prior to operating any machinery or beginning technical procedures."
                }
                for i in range(1, 9)
            ]
        },
        "ai_generated": False
    }
    return fallback_curriculum


# =========================================================================
# AI FEATURE: GENERATE TRAINING PROGRAM FROM COURSE SYLLABUS
# =========================================================================

def generate_training_from_syllabus_ai(
    syllabus_text,
    override_title="",
    duration_hours=None,
    required_seat_time_mins=None,
    slots=30,
    attach_videos=True
):
    """
    Parses an uploaded course syllabus (or raw syllabus text), extracts units of competency,
    lesson topics, objectives, duration, and builds a comprehensive, LMS-ready training course.
    """
    clean_text = (syllabus_text or "").strip()
    if not clean_text:
        raise ValueError("Syllabus content is empty. Please upload a valid document or provide syllabus text.")

    # Truncate if excessively long to avoid token limits
    if len(clean_text) > 40000:
        clean_text = clean_text[:40000] + "\n...[Additional syllabus content truncated]..."

    system_prompt = (
        "You are an expert vocational and academic curriculum designer for TESDA, DOLE, CHED, and the Public Employment Service Office (PESO) in the Philippines. "
        "Your task is to analyze an uploaded Course Syllabus or Training Regulation document and convert it into a complete, structured digital LMS course. "
        "Faithfully adhere to the syllabus topics, modules/units of competency, learning objectives, and procedures outlined in the document. "
        "Output strictly valid JSON with no markdown wrapping."
    )

    prompt = f"""
Carefully analyze this uploaded Course Syllabus / Training Outline:
--- BEGIN SYLLABUS ---
{clean_text}
--- END SYLLABUS ---

Transform this syllabus into a comprehensive digital LMS training course.
Requirements:
1. Title: Detect or extract the official course title from the syllabus{f' (or use: {override_title})' if override_title else ''}.
2. Provider: Institutional training provider, TESDA center, or university mentioned in syllabus (default: "TESDA & PESO Training Center").
3. Duration: Duration hours mentioned in syllabus (e.g., "40 Hours", "80 Hours") or estimated based on content depth.
4. Required Seat-Time: Suggested minimum LMS study minutes (e.g., 60-120 mins).
5. Description: 2-3 paragraph summary of the course rationale, learning outcomes, target beneficiaries, and core proficiencies based on the syllabus.
6. Target Skills: Extract 5-10 specific technical skills, tools, and units of competency taught in this syllabus.
7. Modules:
   - Faithfully extract and progressively structure the course modules / chapters / units of competency from the syllabus.
   - For each module, provide 3 to 10 progressive milestone lessons covering the specific topics, hands-on tasks, and concepts detailed in the syllabus.
   - Provide clear lesson titles (e.g., "1.1 Workshop Safety Protocols", "1.2 Equipment Calibration").
8. Assessment: Provide 8 to 10 practical multiple-choice certification exam questions specifically testing the knowledge, standards, and procedures covered in this syllabus. Each question must include 4 options, the 0-indexed correct option, and an explanation.

Output strictly valid JSON matching this exact structure:
{{
  "title": "Course Title Extracted from Syllabus",
  "provider": "TESDA Regional Training Center",
  "duration": "40 Hours",
  "required_training_minutes": 60,
  "location": "PESO Training Center / Online LMS",
  "description": "Comprehensive course description based on the syllabus...",
  "skills": ["Skill 1", "Skill 2", "Skill 3", "Skill 4", "Skill 5"],
  "curriculum": {{
    "course_overview": "Detailed overview of the syllabus units and milestone lessons...",
    "modules": [
      {{
        "id": "mod-1",
        "title": "Module 1: Title from Syllabus",
        "description": "Module summary from syllabus...",
        "lessons": [
          "1.1 Topic Title",
          "1.2 Topic Title",
          "1.3 Topic Title"
        ]
      }}
    ],
    "assessment": [
      {{
        "id": "q1",
        "question": "Question testing concept from syllabus...",
        "options": ["Correct option", "Distractor 1", "Distractor 2", "Distractor 3"],
        "correct": 0,
        "explanation": "Detailed explanation from syllabus..."
      }}
    ]
  }}
}}
"""

    ai_data = call_gemini_api(prompt, system_instruction=system_prompt, json_mode=True, timeout=60)

    if ai_data and isinstance(ai_data, dict) and "curriculum" in ai_data and ai_data.get("curriculum", {}).get("modules"):
        ai_data["ai_generated"] = True
        if override_title:
            ai_data["title"] = override_title
        if duration_hours:
            ai_data["duration"] = f"{duration_hours} Hours"
        if required_seat_time_mins:
            ai_data["required_training_minutes"] = required_seat_time_mins
        elif not ai_data.get("required_training_minutes"):
            ai_data["required_training_minutes"] = 60

        clean_topic = _clean_program_title(ai_data.get("title", "Vocational Training"))
        curr = ai_data.get("curriculum", {})
        raw_modules = curr.get("modules", [])
        hydrated_modules = []

        for m_idx, raw_mod in enumerate(raw_modules, start=1):
            mod_title = raw_mod.get("title") or f"Module {m_idx}: Competency Phase {m_idx}"
            mod_desc = raw_mod.get("description") or raw_mod.get("summary") or f"Technical competency phase {m_idx}."
            raw_lessons = raw_mod.get("lessons", [])
            if not raw_lessons:
                raw_lessons = [
                    f"{m_idx}.1 Core Fundamentals & Standards",
                    f"{m_idx}.2 Procedural Execution & Safe Handling",
                    f"{m_idx}.3 Practical Exercise & Verification"
                ]

            mod_obj = {
                "id": f"mod-{m_idx}",
                "number": m_idx,
                "title": mod_title,
                "description": mod_desc,
                "summary": mod_desc,
                "lessons": []
            }

            total_les = len(raw_lessons)
            for l_idx, raw_les in enumerate(raw_lessons, start=1):
                if isinstance(raw_les, str):
                    les_title = raw_les.strip()
                    les_summary = f"Practical competency guidelines and technical procedures for {les_title}."
                    if l_idx == total_les and total_les > 2:
                        l_type = "activity"
                    elif l_idx in [1, 3, 5, 7]:
                        l_type = "video"
                    else:
                        l_type = "reading"
                    les_content = {}
                elif isinstance(raw_les, dict):
                    les_title = raw_les.get("title") or f"{m_idx}.{l_idx} Competency Milestone {l_idx}"
                    les_summary = raw_les.get("summary") or raw_les.get("description") or f"Mastering practical execution for {les_title}."
                    l_type = raw_les.get("type") or ("video" if l_idx in [1, 3, 5] else "reading")
                    les_content = raw_les.get("content", {})
                else:
                    les_title = f"{m_idx}.{l_idx} Technical Topic {l_idx}"
                    les_summary = f"Procedures and guidelines for {les_title}."
                    l_type = "reading"
                    les_content = {}

                les_id = f"les-{m_idx}-{l_idx}"
                duration_str = "25 mins" if l_type != "activity" else "15 mins"

                if l_type == "video":
                    aligned_video = get_aligned_video_for_topic(clean_topic, mod_title, les_title) if attach_videos else {}
                    les_content = {
                        "heading": les_title,
                        "overview": les_summary,
                        "video_url": les_content.get("video_url") or aligned_video.get("video_url", ""),
                        "embed_url": les_content.get("embed_url") or aligned_video.get("embed_url", ""),
                        "video_placeholder_title": les_content.get("video_placeholder_title") or aligned_video.get("title", f"Video Tutorial: {les_title}"),
                        "video_duration_str": les_content.get("video_duration_str") or aligned_video.get("duration_str", "20:00 HD"),
                        "channel": aligned_video.get("channel", "Technical Vocational Institute"),
                        "key_points": [
                            f"Live demonstration of {les_title} following industrial standards.",
                            "Critical safety parameters, PPE verification, and equipment calibration.",
                            "Technique precision, tolerance maintenance, and error prevention.",
                            "Post-demonstration inspection and operational documentation."
                        ],
                        "reading_content": _build_rich_reading_content(les_title, les_summary)
                    }
                elif l_type == "activity":
                    les_content = {
                        "heading": f"{les_title} Checkpoint",
                        "overview": f"Validate your technical retention for {mod_title} before progressing.",
                        "quiz_question": f"In {clean_topic}, what is the mandatory protocol when an equipment tolerance drift or anomaly occurs?",
                        "options": [
                            "Halt execution, log the anomaly, inspect parameters, and recalibrate before resuming",
                            "Ignore the drift and force completion to meet deadlines",
                            "Discard all parts and restart without diagnostic evaluation",
                            "Bypass safety switches to restore operation"
                        ],
                        "correct_option_index": 0,
                        "explanation": "Halting operations, logging parameters, and recalibrating prevents cascade defects and guarantees safety compliance."
                    }
                else:
                    les_content = {
                        "heading": les_title,
                        "overview": les_summary,
                        "key_points": [
                            f"Master foundational principles and technical workflows for {les_title}.",
                            "Ensure 100% adherence to Philippine OSH standards and personal safety.",
                            "Execute standard operating procedures with precision tolerances.",
                            "Conduct self-inspection, quality verification, and maintenance logging."
                        ],
                        "reading_content": les_content.get("reading_content") if isinstance(les_content.get("reading_content"), str) and len(les_content.get("reading_content", "")) > 200 else _build_rich_reading_content(les_title, les_summary)
                    }

                mod_obj["lessons"].append({
                    "id": les_id,
                    "title": les_title,
                    "type": l_type,
                    "duration": duration_str,
                    "summary": les_summary,
                    "description": les_summary,
                    "content": les_content
                })

            hydrated_modules.append(mod_obj)

        curr["modules"] = hydrated_modules
        curr["total_lessons"] = sum(len(m["lessons"]) for m in hydrated_modules)

        if not curr.get("assessment") or len(curr.get("assessment", [])) < 5:
            curr["assessment"] = [
                {
                    "id": f"q{i}",
                    "question": f"In {clean_topic}, why is standard operating calibration critical during execution?",
                    "options": [
                        "It prevents operational defects, ensures safety compliance, and maintains precision tolerances",
                        "It is merely a formality and can be omitted during rush shifts",
                        "It is only required for newly purchased equipment",
                        "It replaces the need for personal protective gear"
                    ],
                    "correct": 0,
                    "explanation": "Routine calibration is mandatory to ensure tolerance accuracy and prevent industrial defects."
                }
                for i in range(1, 9)
            ]

        ai_data["curriculum"] = curr
        return ai_data

    # Fallback rule-based parsing if Gemini is offline
    return _fallback_parse_syllabus(
        clean_text=clean_text,
        override_title=override_title,
        duration_hours=duration_hours,
        required_seat_time_mins=required_seat_time_mins,
        attach_videos=attach_videos
    )


def _fallback_parse_syllabus(clean_text, override_title="", duration_hours=None, required_seat_time_mins=None, attach_videos=True):
    """
    Intelligent rule-based parser that scans lines of syllabus text to construct
    modules, lessons, and course overview when Gemini AI API is unavailable.
    """
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    
    # 1. Detect title
    title = override_title
    if not title:
        for line in lines[:10]:
            if any(k in line.lower() for k in ['course title:', 'subject:', 'program:', 'training regulation for']):
                parts = re.split(r'[:\-]', line, maxsplit=1)
                if len(parts) > 1 and len(parts[1].strip()) > 3:
                    title = parts[1].strip()
                    break
        if not title and lines:
            title = lines[0].replace('#', '').strip()
            if len(title) > 60:
                title = title[:60]
    if not title:
        title = "Vocational Competency Training Course"
    title = _clean_program_title(title)

    # 2. Detect duration
    detected_duration = f"{duration_hours or 40} Hours"
    for line in lines[:25]:
        m = re.search(r'(\d+)\s*(?:hours|hrs|hour)', line, re.I)
        if m:
            detected_duration = f"{m.group(1)} Hours"
            break

    # 3. Detect modules / units
    modules_data = []
    current_mod = None

    for line in lines:
        is_mod_header = bool(re.match(r'^(?:module|unit|chapter|week|part)\s*(\d+|[ivx]+)[:\.\-\s]', line, re.I))
        if is_mod_header:
            if current_mod and current_mod['lessons']:
                modules_data.append(current_mod)
            current_mod = {
                'title': line.replace('#', '').strip(),
                'lessons': []
            }
        elif current_mod:
            is_lesson_bullet = bool(re.match(r'^[\-\*\•\d+\.\)]\s*(.+)', line))
            if is_lesson_bullet and len(line) < 120:
                clean_les = re.sub(r'^[\-\*\•\d+\.\)]\s*', '', line).strip()
                if clean_les and clean_les not in current_mod['lessons']:
                    current_mod['lessons'].append(clean_les)

    if current_mod and current_mod['lessons']:
        modules_data.append(current_mod)

    # If no modules detected via regex, create standard progressive modules from syllabus paragraphs
    if not modules_data:
        chunk_size = max(2, len(lines) // 4)
        for i in range(min(4, max(1, len(lines) // chunk_size))):
            m_num = i + 1
            chunk_lines = lines[i * chunk_size:(i + 1) * chunk_size]
            mod_title = f"Module {m_num}: Competency Phase {m_num} — {title}"
            lessons = []
            for cl in chunk_lines[:5]:
                if len(cl) < 100:
                    lessons.append(cl)
            if not lessons:
                lessons = [
                    f"{m_num}.1 Foundational Technical Principles",
                    f"{m_num}.2 Standard Operating Execution",
                    f"{m_num}.3 Practical Inspection Checkpoint"
                ]
            modules_data.append({'title': mod_title, 'lessons': lessons})

    # Hydrate modules
    hydrated_modules = []
    for m_idx, m_raw in enumerate(modules_data, start=1):
        m_title = m_raw.get('title') or f"Module {m_idx}: Core Workflow Phase {m_idx}"
        m_desc = f"Instructional unit and practical competency development for {m_title}."
        m_obj = {
            "id": f"mod-{m_idx}",
            "number": m_idx,
            "title": m_title,
            "description": m_desc,
            "summary": m_desc,
            "lessons": []
        }
        raw_les = m_raw.get('lessons') or [f"{m_idx}.1 Technical Procedures", f"{m_idx}.2 Hands-on Execution"]
        for l_idx, l_str in enumerate(raw_les, start=1):
            les_title = l_str if isinstance(l_str, str) else str(l_str)
            les_summary = f"Guidelines and practical execution for {les_title}."
            l_type = "video" if l_idx in [1, 3] else ("activity" if l_idx == len(raw_les) else "reading")
            duration_str = "25 mins" if l_type != "activity" else "15 mins"

            if l_type == "video":
                aligned_video = get_aligned_video_for_topic(title, m_title, les_title) if attach_videos else {}
                content = {
                    "heading": les_title,
                    "overview": les_summary,
                    "video_url": aligned_video.get("video_url", ""),
                    "embed_url": aligned_video.get("embed_url", ""),
                    "video_placeholder_title": aligned_video.get("title", f"Video Tutorial: {les_title}"),
                    "video_duration_str": aligned_video.get("duration_str", "20:00 HD"),
                    "channel": aligned_video.get("channel", "Technical Vocational Institute"),
                    "key_points": [
                        f"Live demonstration of {les_title} following industrial standards.",
                        "Critical safety parameters, PPE verification, and equipment calibration.",
                        "Technique precision, tolerance maintenance, and error prevention."
                    ],
                    "reading_content": _build_rich_reading_content(les_title, les_summary)
                }
            elif l_type == "activity":
                content = {
                    "heading": f"{les_title} Checkpoint",
                    "overview": f"Validate your technical retention for {m_title}.",
                    "quiz_question": f"In {title}, what is the mandatory first action when conducting technical operations?",
                    "options": [
                        "Perform safety hazard check and don required Personal Protective Equipment (PPE)",
                        "Operate at maximum speed without verification",
                        "Skip equipment calibration checks",
                        "Leave machinery unattended"
                    ],
                    "correct_option_index": 0,
                    "explanation": "Safety hazard inspection and PPE compliance are mandatory before starting any technical operations."
                }
            else:
                content = {
                    "heading": les_title,
                    "overview": les_summary,
                    "key_points": [
                        f"Master foundational principles and technical workflows for {les_title}.",
                        "Ensure adherence to Philippine OSH standards and personal safety.",
                        "Execute standard operating procedures with precision tolerances."
                    ],
                    "reading_content": _build_rich_reading_content(les_title, les_summary)
                }

            m_obj["lessons"].append({
                "id": f"les-{m_idx}-{l_idx}",
                "title": les_title,
                "type": l_type,
                "duration": duration_str,
                "summary": les_summary,
                "description": les_summary,
                "content": content
            })
        hydrated_modules.append(m_obj)

    return {
        "title": title,
        "provider": "TESDA & PESO Training Center",
        "duration": detected_duration,
        "required_training_minutes": required_seat_time_mins or 60,
        "location": "PESO Training Center / Online LMS",
        "description": f"Comprehensive competency-based training course in {title}, constructed from uploaded course syllabus specifications.",
        "skills": [title, "Occupational Safety", "Technical Execution", "Quality Assurance", "Equipment Maintenance"],
        "curriculum": {
            "course_overview": f"Structured course program based on the uploaded syllabus for {title}. Covers {len(hydrated_modules)} modules and {sum(len(m['lessons']) for m in hydrated_modules)} milestone lessons.",
            "modules": hydrated_modules,
            "total_lessons": sum(len(m['lessons']) for m in hydrated_modules),
            "assessment": [
                {
                    "id": f"q{i}",
                    "question": f"In {title}, what is the mandatory standard operating procedure regarding equipment safety?",
                    "options": [
                        "Perform pre-operation inspection, wear prescribed PPE, and calibrate tolerances",
                        "Bypass calibration to accelerate output",
                        "Only inspect machinery once every year",
                        "Disregard protective equipment if experienced"
                    ],
                    "correct": 0,
                    "explanation": "Pre-operation inspection, PPE compliance, and routine calibration are strictly required."
                }
                for i in range(1, 9)
            ]
        },
        "ai_generated": False
    }

