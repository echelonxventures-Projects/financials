"""
Legal document management: ToS, Privacy Policy, DPA, Financial Advice Disclaimer.

All documents are data, not code. They live in the catalogue or settings and can be
uploaded/validated/approved via the platform API. No hard-coded legal text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from financial_model.platform.catalog import CatalogError


@dataclass(frozen=True)
class LegalDocument:
    """A versioned legal document with approval workflow."""

    key: str                    # e.g., "tos", "privacy", "dpa", "financial_disclaimer"
    name: str                   # display name
    version: str                # semantic version or date
    content: str                # Markdown content
    content_hash: str           # SHA-256 for integrity
    required: bool              # must accept before using platform
    approved: bool              # operator has approved this version
    approved_by: str | None     # operator user_id who approved
    approved_at: float | None   # timestamp
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, key: str, name: str, content: str, required: bool = True,
               version: str | None = None, metadata: dict[str, Any] | None = None) -> LegalDocument:
        """Create a new document version."""
        now = datetime.now(timezone.utc).timestamp()
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if version is None:
            version = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return cls(
            key=key,
            name=name,
            version=version,
            content=content,
            content_hash=content_hash,
            required=required,
            approved=False,
            approved_by=None,
            approved_at=None,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

    def approve(self, operator_id: str) -> LegalDocument:
        """Return an approved copy."""
        now = datetime.now(timezone.utc).timestamp()
        return LegalDocument(
            key=self.key,
            name=self.name,
            version=self.version,
            content=self.content,
            content_hash=self.content_hash,
            required=self.required,
            approved=True,
            approved_by=operator_id,
            approved_at=now,
            created_at=self.created_at,
            updated_at=now,
            metadata=self.metadata,
        )

    def to_dict(self, include_content: bool = True) -> dict[str, Any]:
        d = {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "content_hash": self.content_hash,
            "required": self.required,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }
        if include_content:
            d["content"] = self.content
        return d


class LegalStore:
    """File-based storage for legal documents with version history.

    Documents are stored as YAML files under a directory, one file per document key
    containing all versions. This keeps it simple, auditable, and git-friendly.
    """

    BUILTIN_DOCS = {
        "tos": {
            "name": "Terms of Service",
            "required": True,
            "default_content": """# Terms of Service

**Effective Date:** {{effective_date}}

## 1. Acceptance of Terms

By accessing or using the Financial Model Platform ("Platform"), you agree to be bound by these Terms of Service ("Terms"). If you do not agree to these Terms, do not use the Platform.

## 2. Description of Service

The Platform provides financial modeling tools including three-statement models, DCF valuations, LBO analyses, M&A accretion/dilution, and budgeting models. Results are projections based on assumptions you provide.

## 3. User Accounts

### 3.1 Registration
You must provide accurate information and maintain the security of your credentials. You are responsible for all activity under your account.

### 3.2 Account Types
- **Individual**: Single user, personal use
- **Organization**: Multiple users, shared workspace with roles

### 3.3 Subscription Plans
Plans, pricing, and features are described on the pricing page. We may change plans with 30 days' notice.

## 4. Acceptable Use

You agree not to:
- Use the Platform for illegal activities
- Attempt to gain unauthorized access
- Interfere with the Platform's operation
- Reverse engineer or extract source code
- Use outputs for regulated financial advice without proper licensing

## 5. Intellectual Property

### 5.1 Our IP
The Platform, its code, algorithms, and documentation are our proprietary property.

### 5.2 Your Data
You retain ownership of your input data and generated models. We do not claim rights to your financial data.

## 6. Disclaimer of Warranties

THE PLATFORM IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. WE DO NOT WARRANT THAT:
- The Platform will be uninterrupted or error-free
- Results are accurate, complete, or suitable for any purpose
- Financial projections will be achieved

## 7. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, WE SHALL NOT BE LIABLE FOR:
- Indirect, incidental, or consequential damages
- Loss of profits, data, or business opportunities
- Any financial decisions made based on Platform outputs

## 8. Indemnification

You agree to indemnify us from claims arising from your use of the Platform or violation of these Terms.

## 9. Termination

We may suspend or terminate your access for breach of these Terms. You may cancel at any time.

## 10. Governing Law

These Terms are governed by the laws of [Jurisdiction], without regard to conflict of laws.

## 11. Changes to Terms

We may update these Terms. Continued use after changes constitutes acceptance.

## 12. Contact

For questions: legal@{{domain}}
""",
        },
        "privacy": {
            "name": "Privacy Policy",
            "required": True,
            "default_content": """# Privacy Policy

**Effective Date:** {{effective_date}}

## 1. Information We Collect

### 1.1 Account Information
- Email address, name, password hash
- Organization name (for B2B accounts)
- Billing information (processed by payment provider)

### 1.2 Usage Data
- Model configurations and assumptions
- Calculation results and exports
- API requests and timestamps
- Feature usage metrics

### 1.3 Technical Data
- IP address, browser/device info
- Request logs for security and debugging

## 2. How We Use Your Information

- Provide and improve the Platform
- Process billing and subscriptions
- Send service communications (security, billing, legal)
- Analyze usage to improve features
- Comply with legal obligations

## 3. Data Sharing

We do not sell your data. We share only:
- With payment processors (Stripe) for billing
- With subprocessors listed in our DPA
- When required by law or to protect rights

## 4. Data Retention

- Account data: retained while account is active
- Usage logs: 13 months for analytics
- Billing records: 7 years for tax compliance
- Deleted on account closure (except legal holds)

## 5. Your Rights

Depending on jurisdiction, you may have rights to:
- Access, correct, or delete your data
- Export your data (we provide JSON/Excel export)
- Restrict or object to processing
- Withdraw consent (where applicable)

## 6. Security

- TLS encryption in transit
- Encrypted storage for sensitive data
- Regular security assessments
- Incident response procedures

## 7. International Transfers

Data may be processed in countries with adequate protection or under appropriate safeguards.

## 8. Children's Privacy

The Platform is not directed to children under 16.

## 9. Changes

We will notify you of material changes via email or in-platform notice.

## 10. Contact

Data Protection Officer: dpo@{{domain}}
General: privacy@{{domain}}
""",
        },
        "dpa": {
            "name": "Data Processing Agreement",
            "required": False,  # Required for B2B/org accounts
            "default_content": """# Data Processing Agreement (DPA)

**Effective Date:** {{effective_date}}

This DPA forms part of the Terms of Service between {{controller}} ("Controller") and {{processor}} ("Processor").

## 1. Definitions

Terms defined in GDPR Article 4 apply. "Personal Data", "Processing", "Data Subject", "Supervisory Authority" have their GDPR meanings.

## 2. Scope and Nature of Processing

### 2.1 Categories of Data Subjects
- Controller's employees, contractors, and authorized users
- Controller's customers and prospects (if financial data uploaded)

### 2.2 Categories of Personal Data
- Contact information (name, email)
- Authentication credentials (hashed)
- Financial modeling inputs and outputs
- Usage analytics and logs

### 2.3 Purpose of Processing
- Providing the Financial Model Platform
- Billing and subscription management
- Security monitoring and fraud prevention
- Service improvement and analytics

### 2.4 Duration
Processing continues for the term of the agreement and 30 days post-termination for data export.

## 3. Processor Obligations

### 3.1 Processing Only on Instructions
Processor processes Personal Data only on Controller's documented instructions.

### 3.2 Confidentiality
Processor ensures personnel are bound by confidentiality obligations.

### 3.3 Security Measures
Processor implements appropriate technical and organizational measures:
- Encryption in transit (TLS 1.2+) and at rest (AES-256)
- Access controls and authentication
- Regular vulnerability assessments
- Incident response procedures

### 3.4 Subprocessors
Current subprocessors:
- **Stripe** (payment processing) - PCI DSS Level 1
- **Cloud hosting provider** (infrastructure) - SOC 2 Type II
- List maintained at: {{subprocessor_url}}

Controller may object to new subprocessors within 14 days of notice.

### 3.5 Data Subject Rights
Processor assists Controller in fulfilling data subject requests within 14 days.

### 3.6 Breach Notification
Processor notifies Controller within 72 hours of discovering a Personal Data breach.

### 3.7 Data Deletion/Return
On termination, Processor deletes or returns all Personal Data per Controller's instructions.

### 3.8 Audits
Controller may audit Processor's compliance annually with 30 days' notice.

## 4. International Transfers

Transfers outside EEA/UK rely on:
- Adequacy decisions
- Standard Contractual Clauses (SCCs)
- Binding Corporate Rules

## 5. General Provisions

### 5.1 Liability
Each party's liability is per the Terms of Service.

### 5.2 Governing Law
Same as the Terms of Service.

### 5.3 Amendments
This DPA may be amended in writing by both parties.

## 6. Signatures

**Controller:** {{controller_name}}
By: _________________________ Date: ___________

**Processor:** {{processor_name}}
By: _________________________ Date: ___________

## Annex 1: Technical and Organizational Measures

1. Access control: role-based, least privilege
2. Encryption: TLS 1.2+, AES-256 at rest
3. Availability: 99.9% SLA (Enterprise), backups daily
4. Integrity: checksums, audit logs
5. Personnel: background checks, security training
6. Incident response: <4 hour detection, <24 hour notification
""",
        },
        "financial_disclaimer": {
            "name": "Financial Advice Disclaimer",
            "required": True,
            "default_content": """# Financial Advice Disclaimer

**IMPORTANT: READ BEFORE USING THE PLATFORM**

## 1. Not Financial Advice

The Financial Model Platform ("Platform") provides **computational tools and templates** for financial modeling. It does **not** provide:

- Investment advice
- Financial planning advice
- Tax advice
- Legal advice
- Accounting advice
- Regulatory compliance guidance

## 2. Projections Are Not Predictions

All models, valuations, and outputs generated by the Platform are:

- **Mathematical calculations** based on assumptions YOU provide
- **Hypothetical scenarios**, not forecasts of future performance
- **Sensitive to inputs** - small changes can produce large differences
- **Based on simplified models** that cannot capture all real-world complexity

## 3. No Guarantee of Accuracy or Results

We make no representations or warranties that:

- Model outputs will match actual future results
- Valuations reflect fair market value
- Assumptions are appropriate for your situation
- Calculations are error-free

## 4. Your Responsibility

You are solely responsible for:

- Choosing appropriate models and assumptions
- Validating inputs with qualified professionals
- Making your own investment or business decisions
- Complying with applicable laws and regulations
- Obtaining necessary licenses for any advisory activities

## 5. Professional Advice Required

**Before making any financial decision based on Platform outputs, consult:**

- Licensed financial advisor / investment advisor
- Certified public accountant (CPA) or tax advisor
- Legal counsel
- Other qualified professionals as appropriate

## 6. Regulatory Considerations

- The Platform is not registered with any financial regulatory authority
- Outputs may not meet regulatory requirements for formal valuations
- Using the Platform does not satisfy any regulatory modeling requirements

## 7. Limitation of Liability

To the maximum extent permitted by law, the Platform providers shall not be liable for any financial losses, damages, or decisions made based on Platform outputs.

## 8. Acknowledgment

By using the Platform, you acknowledge that you have read, understood, and agree to this disclaimer.

---
*This disclaimer is part of the Terms of Service. Effective: {{effective_date}}*
""",
        },
    }

    REQUIRED_KEYS = {"tos", "privacy", "financial_disclaimer"}
    B2B_REQUIRED_KEYS = {"dpa"}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, list[LegalDocument]] = {}
        self._load_all()

    def _load_all(self) -> None:
        for file in self.path.glob("*.yaml"):
            key = file.stem
            try:
                with file.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict) and "versions" in data:
                    self._cache[key] = [
                        LegalDocument(**v) for v in data["versions"]
                    ]
            except Exception:
                pass  # ignore corrupted files

    def _save(self, key: str) -> None:
        file = self.path / f"{key}.yaml"
        versions = self._cache.get(key, [])
        data = {"versions": [v.to_dict() for v in versions]}
        with file.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, sort_keys=False, default_flow_style=False)

    def ensure_builtins(self, template_vars: dict[str, str] | None = None, auto_approve: bool = True) -> list[LegalDocument]:
        """Create built-in documents if they don't exist. If auto_approve, also approve existing unapproved builtins."""
        created = []
        vars_ = template_vars or {
            "effective_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "domain": "your-domain.com",
            "controller": "Customer",
            "processor": "Platform Provider",
            "controller_name": "Customer Name",
            "processor_name": "Platform Provider Name",
            "subprocessor_url": "https://your-domain.com/subprocessors",
        }

        for key, spec in self.BUILTIN_DOCS.items():
            if key not in self._cache:
                content = spec["default_content"]
                for var, val in vars_.items():
                    content = content.replace(f"{{{{{var}}}}}", val)
                doc = LegalDocument.create(
                    key=key,
                    name=spec["name"],
                    content=content,
                    required=spec["required"],
                )
                if auto_approve:
                    doc = doc.approve("system")
                self._cache[key] = [doc]
                self._save(key)
                created.append(doc)
            elif auto_approve:
                # Re-approve existing unapproved builtin documents
                latest = self.get_latest(key)
                if latest and not latest.approved:
                    approved = latest.approve("system")
                    idx = self._cache[key].index(latest)
                    self._cache[key][idx] = approved
                    self._save(key)
        return created

    def get_latest(self, key: str) -> LegalDocument | None:
        versions = self._cache.get(key, [])
        return versions[-1] if versions else None

    def get_approved(self, key: str) -> LegalDocument | None:
        for doc in reversed(self._cache.get(key, [])):
            if doc.approved:
                return doc
        return None

    def get_all_versions(self, key: str) -> list[LegalDocument]:
        return list(self._cache.get(key, []))

    def get_required_for_kind(self, account_kind: str) -> list[str]:
        required = set(self.REQUIRED_KEYS)
        if account_kind == "organization":
            required |= self.B2B_REQUIRED_KEYS
        return list(required)

    def create_version(self, key: str, content: str, version: str | None = None,
                       required: bool | None = None, metadata: dict[str, Any] | None = None,
                       operator_id: str | None = None, auto_approve: bool = False) -> LegalDocument:
        """Create a new version of a document."""
        latest = self.get_latest(key)
        if latest is None:
            # Use defaults from builtins
            spec = self.BUILTIN_DOCS.get(key, {"name": key.title(), "required": True})
            req = required if required is not None else spec["required"]
        else:
            req = required if required is not None else latest.required

        doc = LegalDocument.create(
            key=key,
            name=latest.name if latest else key.title(),
            content=content,
            required=req,
            version=version,
            metadata=metadata or {},
        )

        if auto_approve and operator_id:
            doc = doc.approve(operator_id)

        self._cache.setdefault(key, []).append(doc)
        self._save(key)
        return doc

    def approve_version(self, key: str, version: str, operator_id: str) -> LegalDocument | None:
        """Approve a specific version."""
        for doc in self._cache.get(key, []):
            if doc.version == version:
                approved = doc.approve(operator_id)
                idx = self._cache[key].index(doc)
                self._cache[key][idx] = approved
                self._save(key)
                return approved
        return None

    def get_acceptance_status(self, account_kind: str, accepted_versions: dict[str, str]) -> dict[str, Any]:
        """Check which required documents have been accepted."""
        required = self.get_required_for_kind(account_kind)
        result = {
            "all_accepted": True,
            "documents": [],
        }

        for key in required:
            approved = self.get_approved(key)
            accepted_version = accepted_versions.get(key)
            doc_info = {
                "key": key,
                "name": approved.name if approved else key,
                "required": True,
                "latest_version": approved.version if approved else None,
                "accepted_version": accepted_version,
                "accepted": accepted_version == (approved.version if approved else None),
            }
            if not doc_info["accepted"]:
                result["all_accepted"] = False
            result["documents"].append(doc_info)

        return result


def validate_legal_content(content: str, doc_type: str) -> list[str]:
    """Validate legal document content. Returns list of warnings/errors."""
    errors = []
    warnings = []

    # Basic checks
    if not content or not content.strip():
        errors.append("Content cannot be empty")
        return errors

    if len(content) < 100:
        warnings.append("Content seems very short for a legal document")

    # Check for placeholder variables
    placeholders = re.findall(r"\{\{(\w+)\}\}", content)
    if placeholders:
        warnings.append(f"Unresolved placeholders: {', '.join(set(placeholders))}")

    # Type-specific checks
    if doc_type == "tos":
        required_sections = ["acceptance", "termination", "liability", "governing law", "changes"]
        for section in required_sections:
            if section.lower() not in content.lower():
                warnings.append(f"Missing recommended section: {section}")

    elif doc_type == "privacy":
        required_sections = ["collect", "use", "share", "retention", "rights", "security", "contact"]
        for section in required_sections:
            if section.lower() not in content.lower():
                warnings.append(f"Missing recommended section: {section}")

    elif doc_type == "dpa":
        required_sections = ["scope", "processor obligations", "subprocessors", "breach", "international transfers"]
        for section in required_sections:
            if section.lower() not in content.lower():
                warnings.append(f"Missing recommended GDPR section: {section}")

    elif doc_type == "financial_disclaimer":
        required_phrases = ["not financial advice", "projections", "consult", "qualified professional", "limitation of liability"]
        for phrase in required_phrases:
            if phrase.lower() not in content.lower():
                warnings.append(f"Missing key disclaimer phrase: '{phrase}'")

    return errors + warnings