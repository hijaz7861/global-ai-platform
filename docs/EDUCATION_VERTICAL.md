# HIJAZ ONE Education Vertical — Version 40 Source Mapping

## Verified source inventory

Read-only inspection of the live Hatchable Version 40 project on 2026-11-22 confirmed:

- 46 database tables, 25 active API functions and 75 project files; the deployed app remains at https://hijaz-construction.hatchable.site.
- Education API files: `api/education.js` and `api/education-admin.js`.
- Source tables: `student_profiles`, `teacher_profiles`, `academies`, `courses`, `course_enrollments`, `course_materials`, and `platform_education_settings`. The source also uses the shared `registration_fee_payments` table.
- The source teacher registration fee is PKR 200. The source course commission setting is nullable/unconfigured in the inspected schema; the portable implementation therefore fails closed for paid courses until a platform administrator configures a commission.
- The source `student_profiles.guardian_consent` is a user-set boolean. It is not accepted as verified guardian authorization in the portable system.

No Hatchable rows, files, identity evidence, credentials or object bytes were exported or copied. The live application was not modified.

## Portable implementation in this Draft slice

- Student and teacher profiles require active, verified-email role entitlements. Teacher fee and 18+ verification remain governed by the central role-entitlement lifecycle; this vertical does not create a second fee/payment authority.
- Student age bands are declarations, not proof. An adult-only course requires an administrator-reviewed age attestation. A student declaring under 18 must have a separate guardian-consent record that is manually verified by an authorized platform administrator before any enrollment or material access.
- Guardian submissions remain pending. A user-supplied boolean, reference, or file is never treated as proof. Verification requires an explicit admin review with evidence checked and a separate review reference; no identity document or guardian contact detail is stored.
- Teacher profiles, courses and course materials are held for moderation. Only a verified teacher with a currently active teacher entitlement may create courses or materials.
- Course prices use integer PKR minor units. Commission basis points are snapshotted when a course is created. Paid courses fail closed while the commission is unset. No payment provider is enabled.
- A payment reference submitted by a student leaves the enrollment in `submitted`; only an authorized administrator's evidence-checked review can mark it verified. Free courses are the only automatically verified enrollments.
- Paid course materials are available only to the enrolled student after payment verification. Public course discovery exposes published course metadata only.

## Source-to-port status

| Hatchable source | Portable target | Status |
|---|---|---|
| `student_profiles` | `EducationStudentProfile` | Draft; age declaration separated from verification |
| `teacher_profiles` | `EducationTeacherProfile` | Draft; teacher role is fee/age authority |
| `courses` | `EducationCourse` | Draft; moderation and PKR minor-unit pricing |
| `course_enrollments` | `EducationCourseEnrollment` | Draft; manual evidence review |
| `course_materials` | `EducationCourseMaterial` | Draft; teacher ownership and moderation |
| `platform_education_settings` | `EducationSettings` | Draft; commission only, initially unset |
| `academies` | — | Not yet ported; no academy/organization mapping is inferred |
| `registration_fee_payments` | central role entitlements | Replaced for teacher activation; no source rows copied |

## Verification and release limits

Focused tests cover role gating, minor guardian consent, adult-course age review, teacher ownership/moderation, commission fail-closed behavior, payment-reference non-activation, manual payment review and protected material access. SQLite and PostgreSQL migration compatibility must be confirmed by the exact-head CI run.

Remaining work: workforce/contractor profiles and hiring; academy/organization mapping; course progress/attendance and live-class provider integration; currency-exponent-aware global pricing; refunds/disputes/settlement; provider-neutral notifications; source-to-target data mapping and full 46-table/object-byte export/import rehearsal; and owner review.

This Draft PR does not merge, deploy, modify Hatchable, copy source data, enable payments or mark HIJAZ ONE production-ready.
