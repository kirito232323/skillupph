"""
SKILLUP Interactive LMS Curriculum & Comprehensive Assessment Engine
Provides Udemy/Cisco-style multi-topic structured modules, interactive lessons,
hands-on exercises, and multi-domain capstone assessments for training programs.
"""
import re
import html
from .ai_services import get_aligned_video_for_topic


def format_reading_markdown_to_html(text):
    """
    Renders structured markdown headers, bold emphasis, and bullet lists
    into semantic, beautifully styled HTML for the LMS course player.
    """
    if not text:
        return ""
    lines = str(text).strip().splitlines()
    html_out = []
    in_list = False

    for line in lines:
        line_str = line.strip()
        if not line_str:
            if in_list:
                html_out.append('</ul>')
                in_list = False
            continue

        # Level 3 Heading
        if line_str.startswith('### '):
            if in_list:
                html_out.append('</ul>')
                in_list = False
            h_text = html.escape(line_str[4:].strip())
            html_out.append(f'<h3 class="text-sm md:text-base font-bold text-on-surface mt-5 mb-2 pb-1.5 border-b border-outline-variant/60 flex items-center gap-2"><span class="w-2 h-4 bg-secondary rounded-full shrink-0"></span><span>{h_text}</span></h3>')
        # Level 4 Heading
        elif line_str.startswith('#### '):
            if in_list:
                html_out.append('</ul>')
                in_list = False
            h_text = html.escape(line_str[5:].strip())
            html_out.append(f'<h4 class="text-xs md:text-sm font-bold text-on-surface mt-3.5 mb-1.5 text-secondary">{h_text}</h4>')
        # Bullet list item
        elif line_str.startswith('- ') or line_str.startswith('* '):
            if not in_list:
                html_out.append('<ul class="space-y-2 my-2.5 pl-4 list-disc marker:text-secondary text-xs md:text-sm text-on-surface">')
                in_list = True
            item_text = line_str[2:].strip()
            item_text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="font-bold text-on-surface">\1</strong>', html.escape(item_text))
            html_out.append(f'<li class="leading-relaxed">{item_text}</li>')
        # Numbered list item
        elif re.match(r'^\d+\.\s+', line_str):
            if in_list:
                html_out.append('</ul>')
                in_list = False
            item_match = re.match(r'^(\d+\.)\s+(.+)$', line_str)
            num_prefix = item_match.group(1) if item_match else '1.'
            item_text = item_match.group(2) if item_match else line_str
            item_text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="font-bold text-on-surface">\1</strong>', html.escape(item_text))
            html_out.append(f'<div class="flex items-start gap-2.5 my-1.5 text-xs md:text-sm text-on-surface leading-relaxed"><span class="font-bold text-secondary shrink-0">{num_prefix}</span><div class="flex-1">{item_text}</div></div>')
        # Standard paragraph
        else:
            if in_list:
                html_out.append('</ul>')
                in_list = False
            p_text = line_str
            p_text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="font-bold text-on-surface">\1</strong>', html.escape(p_text))
            html_out.append(f'<p class="text-xs md:text-sm text-on-surface leading-relaxed mb-3">{p_text}</p>')

    if in_list:
        html_out.append('</ul>')

    return "\n".join(html_out)


def get_default_program_curriculum(training_program):
    title = training_program.title
    skills = [s.name for s in training_program.all_skills]
    main_skill = skills[0] if skills else (training_program.skill_addressed.name if training_program.skill_addressed else "Professional Proficiency")
    provider = training_program.provider or "TESDA & Cisco Academy"
    duration = training_program.duration or "40 Hours"
    desc = training_program.description or f"Comprehensive hands-on training program for {title}."

    # Identify domain flavor (IT, BPO, Engineering, Logistics, Trade, Healthcare, Management)
    title_lower = title.lower()
    skill_lower = main_skill.lower()

    if any(k in title_lower or k in skill_lower for k in ['react', 'python', 'software', 'developer', 'web', 'it', 'cloud', 'data', 'backend', 'frontend', 'network', 'cyber', 'sql']):
        domain_type = 'tech'
    elif any(k in title_lower or k in skill_lower for k in ['weld', 'machin', 'electr', 'automotive', 'carpentry', 'plumb', 'construction', 'trade', 'heavy']):
        domain_type = 'industrial'
    elif any(k in title_lower or k in skill_lower for k in ['customer', 'bpo', 'call', 'support', 'sales', 'marketing', 'relations']):
        domain_type = 'bpo_sales'
    elif any(k in title_lower or k in skill_lower for k in ['admin', 'office', 'assistant', 'management', 'hr', 'finance', 'account']):
        domain_type = 'business'
    elif any(k in title_lower or k in skill_lower for k in ['nurse', 'care', 'health', 'medical', 'hospital', 'food', 'hotel', 'culinary']):
        domain_type = 'service_health'
    else:
        domain_type = 'general'

    modules = [
        {
            "id": "mod_1",
            "number": 1,
            "title": f"Module 1: Foundations & Core Principles of {main_skill}",
            "summary": f"Master the fundamental theory, underlying concepts, and essential terminology required for {title}.",
            "lessons": [
                {
                    "id": "mod1_les1",
                    "type": "video",
                    "title": f"1.1 Course Orientation & Enterprise Context",
                    "duration": "15 min",
                    "description": f"Introduction to {title}, industry requirements, role competencies, and career benchmarks.",
                    "content": {
                        "heading": f"Welcome to {title} — Professional Track",
                        "video_placeholder_title": f"Introduction to {title} & Career Path",
                        "video_duration_str": "15:20 HD",
                        "overview": f"This comprehensive training accredited by {provider} is structured to build end-to-end expertise in {main_skill}. Over the course of these 5 modules, you will explore real-world workflows, examine practical industry case studies, and complete hands-on activities.",
                        "key_points": [
                            f"Understanding industry standards and baseline competencies for {main_skill}.",
                            "Recognizing workflow dependencies, tools of the trade, and core methodologies.",
                            "Preparing for enterprise-grade assignments and proficiency assessments."
                        ],
                        "reading_content": f"""
### Core Principles & Executive Overview
In today's competitive enterprise landscape, mastering **{main_skill}** requires both theoretical command and deep operational fluency. Employers actively seek professionals who understand not just the 'how', but the 'why' behind critical processes.

#### Foundational Pillars:
1. **Precision & Consistency:** Executing standard operating procedures with minimal deviation.
2. **Systemic Thinking:** Understanding how individual tasks impact cross-functional deliverables.
3. **Continuous Verification:** Testing, validating, and auditing outputs prior to delivery.
                        """
                    }
                },
                {
                    "id": "mod1_les2",
                    "type": "reading",
                    "title": f"1.2 Theoretical Frameworks & Essential Architecture",
                    "duration": "25 min",
                    "description": f"Deep dive into standard operating architectures, specifications, and primary terminology.",
                    "content": {
                        "heading": f"Architectural Blueprint for {main_skill}",
                        "overview": f"A structured examination of core specifications, technical lifecycles, and industry guidelines relevant to {title}.",
                        "key_points": [
                            "Core terminology, specifications, and regulatory frameworks.",
                            "Standardized lifecycle phases and execution benchmarks.",
                            "Risk factors, quality control gates, and audit preparedness."
                        ],
                        "reading_content": f"""
### Theoretical Frameworks in Practice
Mastering **{main_skill}** demands familiarity with established industry models. Whether operating in a high-throughput environment or specialized enterprise workflows, adherence to standardized design patterns ensures scalability and safety.

```
[Phase 1: Analysis & Inputs] ➔ [Phase 2: Execution & Synthesis] ➔ [Phase 3: Verification & Output]
```

#### Key Architecture Principles:
- **Modular Isolation:** Break complex workflows into manageable, self-contained units.
- **Fail-Safe Operation:** Design processes to handle unexpected anomalies gracefully without cascading failures.
- **Documentation Standards:** Maintain thorough operational logs for audits and peer reviews.
                        """
                    }
                },
                {
                    "id": "mod1_act1",
                    "type": "activity",
                    "title": "1.3 Interactive Concept Checkpoint",
                    "duration": "10 min",
                    "description": "Test your retention of Module 1 fundamental concepts with an interactive diagnostic check.",
                    "content": {
                        "heading": "Module 1 Knowledge Check",
                        "overview": "Answer the following question to validate your understanding of Module 1 before advancing.",
                        "quiz_question": f"What is the primary objective of establishing standardized operating procedures in {main_skill}?",
                        "options": [
                            "To eliminate the need for supervisory auditing and reporting",
                            "To ensure output consistency, minimize errors, and uphold quality benchmarks",
                            "To reduce overall project scope and bypass safety verifications",
                            "To limit the adoption of modern technological tools"
                        ],
                        "correct_option_index": 1,
                        "explanation": "Standardized procedures establish consistent benchmarks, eliminate variance, and guarantee high-quality deliverables across all workflow executions."
                    }
                }
            ]
        },
        {
            "id": "mod_2",
            "number": 2,
            "title": f"Module 2: Applied Operations & Tooling in {main_skill}",
            "summary": "Hands-on techniques, procedural walk-throughs, and step-by-step practical workflows.",
            "lessons": [
                {
                    "id": "mod2_les1",
                    "type": "video",
                    "title": "2.1 Step-by-Step Workflow Execution",
                    "duration": "30 min",
                    "description": f"Practical walkthrough of primary tools, equipment, software, and operational routines.",
                    "content": {
                        "heading": f"Hands-on Tooling & Execution for {main_skill}",
                        "video_placeholder_title": f"Live Demonstration: Practical {main_skill} Operations",
                        "video_duration_str": "28:45 HD",
                        "overview": f"Watch an expert demonstration illustrating the end-to-end execution of critical {main_skill} tasks, from setup to final quality inspection.",
                        "key_points": [
                            "Setup, environment preparation, and safety calibrations.",
                            "Executing procedural routines following industry guidelines.",
                            "Identifying common bottlenecks and runtime optimizations."
                        ],
                        "reading_content": f"""
### Operational Execution Protocols
Proper execution begins with rigorous preparation. In {main_skill}, 70% of defects arise from inadequate setup or skipping calibration routines.

#### Standard Execution Checklist:
1. **Pre-flight Inspection:** Verify all tooling, credentials, inputs, and environmental constraints.
2. **Execution Phase:** Follow the standardized sequence without taking unverified shortcuts.
3. **Post-execution Sanity Check:** Verify that output parameters match the required specifications.
                        """
                    }
                },
                {
                    "id": "mod2_act1",
                    "type": "activity",
                    "title": "2.2 Practical Simulation & Case Exercise",
                    "duration": "20 min",
                    "description": "Engage with a real-world scenario to apply procedural steps and resolve simulated anomalies.",
                    "content": {
                        "heading": "Practical Sandbox Simulation",
                        "overview": "Examine the practical scenario below and select the correct operational adjustment.",
                        "quiz_question": f"During live operation in a {main_skill} workflow, you encounter an unexpected variance in performance output. What is the immediate correct protocol?",
                        "options": [
                            "Ignore the variance and force completion to meet deadlines",
                            "Halt execution, log the anomaly, inspect input parameters, and perform diagnostic calibration",
                            "Delete the entire dataset or scrap all materials immediately without diagnosis",
                            "Change the quality standard threshold to make the variance acceptable"
                        ],
                        "correct_option_index": 1,
                        "explanation": "Standard protocol requires halting, recording the event, analyzing root causes through calibration, and confirming fix validity before resuming operations."
                    }
                }
            ]
        },
        {
            "id": "mod_3",
            "number": 3,
            "title": "Module 3: Industry Standards, Compliance & Quality Assurance",
            "summary": "Safety regulations, compliance frameworks, peer review standards, and quality management.",
            "lessons": [
                {
                    "id": "mod3_les1",
                    "type": "reading",
                    "title": "3.1 Compliance Guidelines & Quality Control Gates",
                    "duration": "25 min",
                    "description": "Understanding statutory regulations, employer quality standards, and compliance audits.",
                    "content": {
                        "heading": "Quality Assurance & Regulatory Compliance",
                        "overview": "Learn how regulatory bodies, ISO/TESDA standards, and enterprise QA protocols enforce excellence.",
                        "key_points": [
                            "Statutory compliance and workplace safety/security protocols.",
                            "Implementing multi-stage quality assurance gates.",
                            "Audit trails, certification readiness, and accountability."
                        ],
                        "reading_content": f"""
### Quality Control & Compliance Architecture
Quality Assurance (QA) is not an afterthought; it is woven into every stage of professional practice in **{main_skill}**.

#### The QA Triad:
- **Preventative Controls:** Clear checklists, pre-requisite validation, and automated linting/guards.
- **Detective Controls:** Peer reviews, integration tests, automated alerts, and physical stress tests.
- **Corrective Controls:** Structured incident response, retrospective reviews, and preventative SOP updates.
                        """
                    }
                },
                {
                    "id": "mod3_act1",
                    "type": "activity",
                    "title": "3.2 Compliance Scenario Analysis",
                    "duration": "15 min",
                    "description": "Evaluate a workplace compliance scenario to identify potential risks and mitigation steps.",
                    "content": {
                        "heading": "Compliance & Audit Readiness Check",
                        "overview": "Assess the compliance violation scenario and identify the proper mitigation step.",
                        "quiz_question": "Why is maintaining an immutable audit log and documentation trail critical for enterprise operations?",
                        "options": [
                            "It enables traceability, facilitates root cause analysis, and fulfills legal compliance mandates",
                            "It is solely used to assign personal blame to team members",
                            "It increases paperwork to intentionally slow down project delivery",
                            "It replaces the need for actual functional testing"
                        ],
                        "correct_option_index": 0,
                        "explanation": "Audit trails provide verifiable transparency, regulatory compliance, and factual data necessary for ongoing process improvement."
                    }
                }
            ]
        },
        {
            "id": "mod_4",
            "number": 4,
            "title": "Module 4: Advanced Problem Solving & Enterprise Workflows",
            "summary": "Troubleshooting methodologies, critical thinking scenarios, and scaling operational output.",
            "lessons": [
                {
                    "id": "mod4_les1",
                    "type": "video",
                    "title": "4.1 Root Cause Analysis & Advanced Troubleshooting",
                    "duration": "30 min",
                    "description": "Apply systemic troubleshooting frameworks (5 Whys, Fishbone, Diagnostic Trees) to resolve complex issues.",
                    "content": {
                        "heading": "Advanced Problem Solving Frameworks",
                        "video_placeholder_title": "Masterclass: Enterprise Troubleshooting & Triage",
                        "video_duration_str": "32:10 HD",
                        "overview": "Explore advanced diagnostic techniques to isolate edge cases and eliminate recurring vulnerabilities in complex environments.",
                        "key_points": [
                            "Applying the 5 Whys and Fishbone root-cause frameworks.",
                            "Differentiating between symptoms and core system failures.",
                            "Documenting post-mortem learnings and enterprise preventive measures."
                        ],
                        "reading_content": f"""
### Enterprise Troubleshooting Masterclass
Senior practitioners in **{main_skill}** distinguish themselves through rapid root-cause isolation. 

```
[Incident Detected] ➔ [Triage & Containment] ➔ [Root Cause Isolation] ➔ [Permanent Remediation] ➔ [Post-Mortem Review]
```

#### Key Diagnostic Steps:
1. **Isolate the Variable:** Change one parameter at a time to determine the exact failure trigger.
2. **Reproduce in Controlled Environment:** Never debug unverified fixes in production/live systems.
3. **Implement Guardrails:** Prevent recurring failures by adding automated checks or physical interlocks.
                        """
                    }
                },
                {
                    "id": "mod4_act1",
                    "type": "activity",
                    "title": "4.2 High-Stakes Scenario Challenge",
                    "duration": "20 min",
                    "description": "Interactive multi-step decision tree simulating a high-urgency production issue.",
                    "content": {
                        "heading": "Critical Incident Triage Exercise",
                        "overview": "Analyze the high-severity incident and choose the best strategic course of action.",
                        "quiz_question": "When a major operational bottleneck threatens a critical project deadline, what is the most effective approach?",
                        "options": [
                            "Rush unverified changes directly into the live environment without review",
                            "Conduct rapid triage, isolate the bottleneck, implement a verified fallback, and notify stakeholders with an ETA",
                            "Abandon the project and blame external vendors",
                            "Conceal the bottleneck and wait for clients to notice"
                        ],
                        "correct_option_index": 1,
                        "explanation": "Effective engineering and professional practice demands clear communication, systematic triage, and verified fallbacks."
                    }
                }
            ]
        },
        {
            "id": "mod_5",
            "number": 5,
            "title": f"Module 5: Comprehensive Multi-Area Assessment & Capstone Exam",
            "summary": "Multi-topic comprehensive examination evaluating all 4 core competency domains for official certification.",
            "lessons": [
                {
                    "id": "mod5_exam",
                    "type": "assessment",
                    "title": "5.1 Capstone Multi-Domain Certification Exam",
                    "duration": "45 min",
                    "description": f"Comprehensive 4-domain examination. Achieving 70%+ unlocks official {provider} certification.",
                    "content": {
                        "heading": f"Official Certification Exam — {title}",
                        "overview": f"This comprehensive assessment evaluates your proficiency across all 4 key knowledge domains for {main_skill}. You must score at least 70% to complete the training, earn your downloadable Certificate of Completion, and add verified credentials to your profile.",
                        "passing_score": 70,
                        "domains": [
                            "Domain 1: Core Theoretical Foundations",
                            "Domain 2: Applied Operations & Tooling",
                            "Domain 3: Quality Assurance & Compliance",
                            "Domain 4: Advanced Problem Solving & Scenarios"
                        ],
                        "questions": [
                            {
                                "id": "q1",
                                "domain": "Domain 1: Core Theoretical Foundations",
                                "question": f"In the context of {main_skill}, what is the fundamental purpose of adhering to standard architectural principles?",
                                "options": [
                                    "To minimize structural complexity, reduce error rates, and support maintainability",
                                    "To increase runtime latency and require proprietary tooling",
                                    "To restrict team members from making ongoing improvements",
                                    "To bypass regulatory oversight and quality control audits"
                                ],
                                "correct": 0,
                                "explanation": "Architectural principles establish clarity, modularity, and operational reliability across systems."
                            },
                            {
                                "id": "q2",
                                "domain": "Domain 1: Core Theoretical Foundations",
                                "question": "Which phase of the standard operational lifecycle focuses on validating input requirements before active processing begins?",
                                "options": [
                                    "Deployment / Commissioning Phase",
                                    "Pre-flight Analysis & Verification Phase",
                                    "Post-mortem Retrospective Phase",
                                    "Archival & Decommissioning Phase"
                                ],
                                "correct": 1,
                                "explanation": "The Pre-flight Analysis phase ensures all required inputs and constraints are satisfied before beginning work."
                            },
                            {
                                "id": "q3",
                                "domain": "Domain 2: Applied Operations & Tooling",
                                "question": f"When configuring tools for {main_skill}, why is routine calibration and environment verification mandatory?",
                                "options": [
                                    "To artificially delay project schedules",
                                    "To guarantee precision tolerances and prevent systemic operational defects",
                                    "To consume excess inventory and power",
                                    "To bypass documentation logs"
                                ],
                                "correct": 1,
                                "explanation": "Calibration prevents measurement drift and guarantees adherence to precision engineering standards."
                            },
                            {
                                "id": "q4",
                                "domain": "Domain 2: Applied Operations & Tooling",
                                "question": "What is the recommended protocol when an unexpected anomaly occurs during procedural execution?",
                                "options": [
                                    "Immediately halt, log the anomaly parameters, and run diagnostic isolation",
                                    "Increase operational velocity to outpace the error",
                                    "Turn off error reporting alerts to suppress notifications",
                                    "Continue running without recording the incident"
                                ],
                                "correct": 0,
                                "explanation": "Halting and documenting anomalies prevents cascade failures and provides data for root-cause analysis."
                            },
                            {
                                "id": "q5",
                                "domain": "Domain 3: Quality Assurance & Compliance",
                                "question": "Which of the following represents a proactive (preventative) Quality Assurance control?",
                                "options": [
                                    "Customer complaints analysis after product delivery",
                                    "Automated pre-commit validation checks and standardized checklists",
                                    "Emergency rollbacks following a production crash",
                                    "Warranty claim processing"
                                ],
                                "correct": 1,
                                "explanation": "Pre-commit checks and checklists prevent defects from entering the pipeline in the first place."
                            },
                            {
                                "id": "q6",
                                "domain": "Domain 3: Quality Assurance & Compliance",
                                "question": "Why are verifiable audit logs essential for industry compliance certifications (e.g., ISO, TESDA, DOLE)?",
                                "options": [
                                    "They serve as non-repudiable proof of procedural adherence and safety compliance",
                                    "They are discarded immediately after shift handover",
                                    "They are only kept for marketing brochures",
                                    "They replace physical equipment maintenance"
                                ],
                                "correct": 0,
                                "explanation": "Immutable audit trails establish legal compliance, transparency, and traceability for regulatory standards."
                            },
                            {
                                "id": "q7",
                                "domain": "Domain 4: Advanced Problem Solving & Scenarios",
                                "question": "In the '5 Whys' root-cause methodology, what is the primary goal of repeatedly probing a failure?",
                                "options": [
                                    "To find a single person to reprimand",
                                    "To penetrate past superficial symptoms and address the underlying systemic flaw",
                                    "To generate unnecessary paperwork",
                                    "To prove that processes can never fail"
                                ],
                                "correct": 1,
                                "explanation": "The 5 Whys technique drills past surface symptoms to discover the underlying root cause."
                            },
                            {
                                "id": "q8",
                                "domain": "Domain 4: Advanced Problem Solving & Scenarios",
                                "question": "When balancing urgency with reliability during high-severity enterprise incidents, what is the best strategy?",
                                "options": [
                                    "Implement unreviewed, untested code or actions directly on live systems",
                                    "Contain the issue, activate a tested fallback/workaround, and verify the permanent fix in staging",
                                    "Refuse to acknowledge the incident until full resolution is achieved",
                                    "Blame end-users for discovering the malfunction"
                                ],
                                "correct": 1,
                                "explanation": "Structured incident management isolates the impact using fallbacks while validating permanent resolutions in staging."
                            }
                        ]
                    }
                }
            ]
        }
    ]

    # Pre-render markdown to HTML for reading content
    for mod in modules:
        for les in mod.get("lessons", []):
            if isinstance(les.get("content"), dict) and les["content"].get("reading_content"):
                les["content"]["reading_content_html"] = format_reading_markdown_to_html(les["content"]["reading_content"])

    # Calculate total lessons and activities count
    total_lessons = 0
    for mod in modules:
        total_lessons += len(mod["lessons"])

    return {
        "program_id": training_program.id,
        "title": title,
        "provider": provider,
        "duration": duration,
        "skills": skills,
        "main_skill": main_skill,
        "description": desc,
        "domain_type": domain_type,
        "modules": modules,
        "total_lessons": total_lessons,
    }


def get_program_curriculum(training_program):
    """
    Returns the training curriculum. If a custom curriculum has been created
    or edited by the Admin in the UI, returns the custom curriculum data.
    Otherwise, generates and returns the standardized domain-tailored curriculum.
    """
    if training_program.custom_curriculum and isinstance(training_program.custom_curriculum, dict):
        custom = training_program.custom_curriculum
        if "modules" in custom and isinstance(custom["modules"], list) and len(custom["modules"]) > 0:
            # Check if any lesson is already an assessment
            def _has_questions(l):
                c = l.get("content")
                return (isinstance(c, dict) and bool(c.get("questions"))) or bool(l.get("questions"))

            has_assessment = any(
                les.get("type") == "assessment" or _has_questions(les)
                for m in custom["modules"]
                for les in m.get("lessons", [])
            )
            # If assessment questions exist in custom root but not inside a lesson, attach to the last module
            if not has_assessment and custom.get("assessment") and isinstance(custom.get("assessment"), list):
                last_mod = custom["modules"][-1]
                if "lessons" not in last_mod or not isinstance(last_mod["lessons"], list):
                    last_mod["lessons"] = []
                last_mod["lessons"].append({
                    "id": f"{last_mod.get('id', 'mod')}_exam",
                    "type": "assessment",
                    "title": "Comprehensive Capstone Certification Exam",
                    "duration": "30 mins",
                    "summary": "Mandatory final competency exam for course completion and certificate issuance.",
                    "description": "Comprehensive multiple-choice assessment to validate competency.",
                    "content": {
                        "heading": "Competency Certification Exam",
                        "overview": "Answer all questions to validate proficiency and unlock your official Certificate of Completion.",
                        "questions": custom["assessment"]
                    }
                })

            # Normalize modules and lessons
            for mod_idx, mod in enumerate(custom["modules"]):
                if not mod.get("summary") and mod.get("description"):
                    mod["summary"] = mod["description"]
                elif not mod.get("description") and mod.get("summary"):
                    mod["description"] = mod["summary"]
                for les_idx, les in enumerate(mod.get("lessons", [])):
                    if not les.get("type"):
                        if _has_questions(les):
                            les["type"] = "assessment"
                        else:
                            les["type"] = "reading"
                    if not les.get("description") and les.get("summary"):
                        les["description"] = les["summary"]
                    elif not les.get("summary") and les.get("description"):
                        les["summary"] = les["description"]
                    if not les.get("duration"):
                        les["duration"] = "20 min"

                    # Normalize content into a rich dict if it is a string or minimal dict
                    c_val = les.get("content")
                    les_title = les.get("title", "Technical Competency Guide")
                    les_summary = les.get("summary") or les.get("description", "")
                    
                    def _build_rich_reading_content(headline_text, summary_text):
                        clean_lead = summary_text.strip() if summary_text.strip() else f"Comprehensive practical guide and operational standards for {headline_text}."
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

                    if isinstance(c_val, str) and c_val.strip():
                        txt = c_val.strip()
                        les["content"] = {
                            "heading": les_title,
                            "overview": les_summary or txt[:220],
                            "key_points": [
                                f"Master core operational principles and concepts for {les_title}.",
                                "Ensure complete PPE compliance and pre-operation workspace safety check.",
                                "Follow technical step-by-step procedures according to Philippine vocational standards.",
                                "Perform thorough self-inspection, quality verification, and post-task documentation."
                            ],
                            "reading_content": txt if len(txt) >= 280 else _build_rich_reading_content(les_title, txt)
                        }
                    elif isinstance(c_val, dict):
                        if not c_val.get("heading"):
                            c_val["heading"] = les_title
                        if not c_val.get("overview"):
                            c_val["overview"] = les_summary or f"Competency overview for {les_title}."
                        if not c_val.get("key_points"):
                            c_val["key_points"] = [
                                f"Understand key workflows and best practices for {les_title}.",
                                "Maintain safety compliance and standard equipment calibration.",
                                "Execute hands-on procedures with industry-grade quality and accuracy.",
                                "Review lesson key takeaways and prepare for the final certification exam."
                            ]
                        existing_rc = str(c_val.get("reading_content", "")).strip()
                        if not existing_rc or len(existing_rc) < 200:
                            c_val["reading_content"] = _build_rich_reading_content(les_title, existing_rc or c_val.get("overview") or les_summary)
                    elif not c_val:
                        les["content"] = {
                            "heading": les_title,
                            "overview": les_summary or f"Technical guide for {les_title}.",
                            "key_points": [
                                f"Master operational standards for {les_title}.",
                                "Maintain safety compliance and standard equipment calibration.",
                                "Execute hands-on procedures according to technical standards.",
                                "Verify task quality and completion."
                            ],
                            "reading_content": _build_rich_reading_content(les_title, les_summary)
                        }

            custom["program_id"] = training_program.id
            custom["title"] = custom.get("title") or training_program.title
            custom["provider"] = custom.get("provider") or training_program.provider
            custom["duration"] = custom.get("duration") or training_program.duration
            custom["description"] = custom.get("description") or training_program.description or f"Comprehensive training program for {training_program.title}."
            return normalize_curriculum_videos_and_content(custom, training_program.title)

    default_curr = get_default_program_curriculum(training_program)
    return normalize_curriculum_videos_and_content(default_curr, training_program.title)


def normalize_curriculum_videos_and_content(curriculum_data, program_title):
    """
    Ensures every video lesson across all modules has a distinct, working, topic-aligned
    YouTube video (Module 1: Foundations, Module 2: Applied Operations, Module 4: Troubleshooting),
    guaranteeing that no two modules within the same training program display the same video.
    Also ensures reading markdown content is pre-rendered to rich HTML.
    """
    broken_placeholders = {
        "cI7U7mO197I", "F3a37yKk7gA", "OGJ_8z78H0o", "3sWvK6o8p5g", "k1_iS88gR4w",
        "H7bZ9C6w7bQ", "vV3fL8uHk4U", "9_C8n3L59q8", "f2n8n7JbE6g", "Z61E7-7eFm8",
        "UqN9hGqH6Yk", "N6_f0vG39qM", "1uX7t5nB8Fk", "6v4A9eK7xJw", "rWlD5t1y2B0",
        "2r7d7G_m3oQ", "7h2Y9Ym89hQ", "p4f4Yh_y0Vw", "W0Q6w_639vM", "aG9R2w6l2hE",
        "GjYJp3VfO1w", "G3e-cpL7ofc", "2v3H1zE5kKw", "kYJjZ3S7e3g", "Gmic1345nM0",
    }

    used_video_urls = set()
    modules = curriculum_data.get("modules", [])

    video_lesson_idx = 0
    for mod_idx, mod in enumerate(modules):
        mod_title = mod.get("title", f"Module {mod_idx + 1}")
        for les_idx, les in enumerate(mod.get("lessons", [])):
            les_title = les.get("title", "")
            if les.get("type") == "video":
                video_lesson_idx += 1
                if not isinstance(les.get("content"), dict):
                    les["content"] = {}

                current_vurl = (les["content"].get("video_url") or "").strip()
                is_broken = (
                    not current_vurl
                    or "listType=search" in current_vurl
                    or any(b_id in current_vurl for b_id in broken_placeholders)
                    or (current_vurl in used_video_urls)  # Prevent duplicate video on every module!
                )

                if is_broken:
                    aligned_vid = get_aligned_video_for_topic(
                        topic=program_title,
                        module_title=mod_title,
                        lesson_title=les_title,
                        used_urls=used_video_urls,
                        lesson_index=video_lesson_idx,
                        module_num=mod_idx + 1
                    )
                    les["content"]["video_url"] = aligned_vid.get("video_url", "")
                    les["content"]["embed_url"] = aligned_vid.get("embed_url", "")
                    les["content"]["video_placeholder_title"] = aligned_vid.get("title", f"Video Masterclass: {les_title}")
                    les["content"]["video_duration_str"] = aligned_vid.get("duration_str", "20:00 HD")
                    les["content"]["channel"] = aligned_vid.get("channel", "Technical Vocational Institute")
                    used_video_urls.add(aligned_vid.get("video_url", ""))
                else:
                    used_video_urls.add(current_vurl)

            # Pre-render reading content markdown to rich HTML
            if isinstance(les.get("content"), dict) and les["content"].get("reading_content"):
                if not les["content"].get("reading_content_html"):
                    les["content"]["reading_content_html"] = format_reading_markdown_to_html(les["content"]["reading_content"])

    tot = sum(len(m.get("lessons", [])) for m in modules)
    curriculum_data["total_lessons"] = tot
    return curriculum_data

