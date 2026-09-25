# Mockup-001: Dynamiq AI Management Console

| Field | Value |
|-------|-------|
| อ้างอิง | PRD-001 r10 (`docs/prd/prd-001-dynamiq-ai-console.*.md`) |
| สถานะ | Draft — mockup สำหรับคุย scope และ UX กับทีม |
| วันที่ | 2026-09-25 |
| ขนาดหน้าจอ | Desktop 1440 × 900 |
| ต้นฉบับ | Design canvas "Dynamiq AI Console Mockup" (claude.ai) — กด Play เพื่อคลิกดูได้ |

> ตัวเลข ชื่อ agent และรายการธุรกรรมทั้งหมดเป็น **ข้อมูลตัวอย่าง** · ค่าที่อยู่ในวงเล็บเหลี่ยม เช่น `[gateway-host]`, `[ชื่อผู้ใช้]`, `[ใส่ราคา]` คือช่องที่ต้องเติมข้อมูลจริง

## สารบัญหน้าจอ

| # | หน้าจอ | เมนู (PRD 6.9) | Phase |
|---|--------|----------------|-------|
| 1 | Dashboard | Dashboard | 1 |
| 2 | Agents catalog | Agents | 1 |
| 3 | Agent editor — Identity | Agents | 1 |
| 4 | Agent editor — Model | Agents | 1 |
| 5 | Playground + Trace | Playground | 1 |
| 6 | Runs / Logs | Runs / Logs | 1 |
| 7 | Runs / Logs — run ที่ error | Runs / Logs | 1 |
| 8 | Deployment detail | Deployments | 1 |
| 9 | API Keys | API Keys | 1 |
| 10 | Connections | Connections | 1 |
| 11 | Settings — Users | Settings | 1 |
| 12 | Settings — Companies | Settings (platform_admin) | 1 |
| 13 | Tools | Tools | 2 |
| 14 | Knowledge Bases | Knowledge Bases | 2 |
| 15 | Skills | Skills | 2 |
| 16 | Evaluation | Evaluation | 3 |
| 17 | Workflows | Workflows | 4 |

## ส่วนร่วมทุกหน้า

- **Sidebar (ซ้าย):** กลุ่ม WORKSPACE (Phase 1) และ LATER PHASES (มีป้าย P2/P3/P4) · ด้านล่างแสดงผู้ใช้และ role
- **Header:** breadcrumb `บริษัท / เมนู`, ชื่อหน้า, ปุ่ม action หลักด้านขวา
- **Company switcher:** อยู่บน Dashboard — ทุก request ส่ง `X-Company-Id` (PRD 7.2)
- **Visual language:** พื้น `#F4F3EF`, sidebar `#16181B`, สีหลัก teal `#0E6B66`, amber = เตือน, แดง = error/block · ฟอนต์ IBM Plex Sans Thai + IBM Plex Mono
- **Accessibility:** ปุ่มและ input เป็น element จริง, มี `:focus-visible` ทุกหน้า, สถานะไม่ได้บอกด้วยสีอย่างเดียว (มีข้อความกำกับเสมอ)

---

## 1. Dashboard

![Dashboard](screens/01-dashboard.png)

**อ้างอิง PRD:** 6.8

- KPI 5 ช่อง: Runs วันนี้, Tokens เดือนนี้ (in/out), Cost เดือนนี้ + ประมาณการสิ้นเดือน, Error rate (แยกสาเหตุ), Guardrail triggers (block/mask/flag)
- กราฟ Runs ต่อวัน 14 วัน (เลือกช่วงเวลาได้)
- Quota วันนี้ต่อ deployment — แถบเปลี่ยนเป็น amber เมื่อ ≥ 80% พร้อมกล่องแจ้งเตือน, reset 00:00 ICT
- ตาราง Agents เดือนนี้ (runs, p95 latency, cost, error) → ลิงก์ไป Agents catalog
- Guardrail events ล่าสุด → ลิงก์ไป Runs / Logs
- platform_admin: มุมมองรวมทุกบริษัท (ยังไม่ได้วาด)

## 2. Agents catalog

![Agents catalog](screens/02-agents-catalog.png)

**อ้างอิง PRD:** 6.1a (Catalog), 6.9

- การ์ดต่อ agent: `display_name`, `name`, version (published / draft), `role`, `responsibilities`, `owner`, `languages`, จำนวน deployment
- ค้นหาตามชื่อ / role / tag และกรองตาม owner
- คลิกการ์ด → Agent editor · การ์ดสุดท้าย "สร้าง agent ใหม่ — เริ่มจากแท็บ Identity"

## 3. Agent editor — แท็บ Identity

![Agent editor — Identity](screens/03-agent-identity.png)

**อ้างอิง PRD:** 6.1, 6.1a

- แท็บตามลำดับ PRD: **Identity** · Model · Tools · Knowledge · Skills · Memory · Guardrails · Advanced
- แถบสถานะ compile: spec_version, compiler, dynamiq version, allowlist ของ role, ตรวจว่า connection/KB/skill ทั้งหมดเป็นของบริษัทเดียวกัน
- ฟอร์ม Identity: `display_name`, `name` (slug, แก้ไม่ได้), `owner`, `role`, `languages` (ภาษาหลัก), `persona`, `tags`
- กล่อง `responsibilities`, `in_scope`, `out_of_scope`
- `handoff`: เงื่อนไข → ช่องทาง (เพิ่มได้หลายข้อ)
- หมายเหตุ: compiler ประกอบ identity เป็น system prompt จาก template กลาง; `GET /info` คืนเฉพาะ `display_name`, `role`, `languages` (ไม่รวม instructions)
- แผงทดสอบด้านขวา: ตัวอย่างการตอบตามปกติ และการปฏิเสธคำขอที่อยู่ใน `out_of_scope`
- Header: Versions, Save draft, Publish

## 4. Agent editor — แท็บ Model

![Agent editor — Model](screens/04-agent-model.png)

**อ้างอิง PRD:** 6.1, 7.7

- Primary model: Connection (เลือกได้เฉพาะของบริษัท), Model, Temperature, Max tokens
- Fallback model: เปิด/ปิด, Connection + Model — ใช้เมื่อ 429/5xx หลัง retry 2 ครั้ง
- Run limits: `max_loops` (สูงสุด 20), timeout, output mode

> แท็บ Tools, Knowledge, Skills, Memory, Guardrails, Advanced ยังไม่ได้ออกแบบใน mockup นี้

## 5. Playground + Trace

![Playground](screens/05-playground.png)

**อ้างอิง PRD:** 6.2, 6.4, 4.4-B

- เลือก agent + version (draft ได้), New conversation, แสดง `conversation_id` และ `playground=true`
- ข้อความผู้ใช้ที่มี PII: แสดงป้าย "ส่งให้ model แบบ masked" — mask ตั้งแต่ขา input
- คำตอบแสดงป้าย guardrail และ `done · latency · cost`
- มี 2 สถานะ (tweak `runState`): **done** / **streaming** — ระหว่าง stream มีปุ่ม Stop และ Send ถูก disable
- Trace: Latency, Tokens in/out, Cost, Loops · ลำดับ step: guardrail → thought → tool_call → tool_result → retrieval → llm (primary/fallback) → output guardrail → final · step ของ Phase 2 มีป้าย P2
- "+ Add as test case" (ต่อกับ Evaluation), ลิงก์ไป Runs / Logs ด้วย `trace_id`

## 6. Runs / Logs

![Runs / Logs](screens/06-runs.png)

**อ้างอิง PRD:** 6.8, 8.2 (`logs.runs`)

- ตัวกรอง: ค้นหา (run id, trace_id, conversation_id, external_user_id), Agent, Deployment, Source, Status, ช่วงเวลา
- ตาราง: เวลา, agent + version, source (api/playground/eval), status (success/error/blocked/interrupted), latency, tokens, cost · คลิกแถวเพื่อดูรายละเอียด
- แผงรายละเอียด: agent, deployment, user, model, `agent_name`, identity version · แท็บ Trace / Conversation / Guardrail events · ด้านล่างแสดง latency, tokens, cost และ guardrail cost แยกกัน

## 7. Runs / Logs — run ที่ error

![Runs — error](screens/07-runs-error.png)

- กล่อง error อธิบายลำดับ: provider 429 → retry 2 ครั้ง → fallback → เกิน timeout 120s
- Trace แสดงแต่ละ step ของ retry และ fallback

## 8. Deployment detail

![Deployment detail](screens/08-deployments.png)

**อ้างอิง PRD:** 6.3, 9.4

- Header: slug `<company_code>-<name>`, environment, สถานะ, Rollback, Save changes
- Binding: agent, published version, `config_version`
- API keys ที่มี scope ถึง deployment นี้ (อ่านอย่างเดียว) → "จัดการใน API Keys"
- Limits: `rate_limit_per_min`, `daily_token_limit`, `daily_cost_limit_usd` + แถบใช้ไปวันนี้ (รวม reserved), เตือนที่ 80%, Redis down → quota fail closed
- Runtime & security: `allowed_origins`, `output_mode`, `conversation_ttl_days`, อนุญาต write tools, guardrail override
- ตัวอย่างเรียกใช้: cURL / PHP / Node / `GET /info`

## 9. API Keys

![API Keys](screens/09-api-keys.png)

**อ้างอิง PRD:** 6.3, 7.7, 9.1 (`/api-keys`)

- ตาราง key ของบริษัท: name + ผู้สร้าง, key (`ak_advws_••••`), scope ต่อ deployment, `allowed_ips`, limit ต่อ key, last used, Revoke · key ที่ revoke แล้วแสดงจางลง
- ฟอร์มสร้าง key: name, เลือก scope ได้เฉพาะ deployment ของบริษัท, `rate_limit_per_min`, `daily_cost_limit_usd`, `allowed_ips`
- กล่อง "แสดงครั้งเดียว" สำหรับ key ที่เพิ่งสร้าง
- หลักการ: prefix บอกเจ้าของ · เรียก slug นอก scope หรือของบริษัทอื่น = 404 · ใช้ server-to-server เท่านั้น (browser ใช้ session token 5 นาที)

## 10. Connections

![Connections](screens/10-connections.png)

**อ้างอิง PRD:** 7.3, 7.4, 7.7

- รายการ connection ของบริษัท (ไม่มี shared connection ข้ามบริษัท) พร้อมสถานะทดสอบล่าสุด
- ฟอร์ม: Name, Provider type, `api_base` + ผลตรวจ egress allowlist, `max_concurrency`
- API key แบบ masked + Replace key · envelope encryption ด้วย DEK ของบริษัท
- Test connection + ผล
- "ใช้งานโดย": agents ที่อ้างถึง · อ้างข้ามบริษัท = compile ไม่ผ่าน · ลบไม่ได้ขณะมี agent ใช้
- ตัวอย่างกรณีปฏิเสธ: `api_base` เป็น private IP, key ไม่ถูกต้อง (401)

## 11. Settings — Users

![Settings — Users](screens/11-settings-users.png)

**อ้างอิง PRD:** 7.1, 7.2

- แท็บ: Users · Egress allowlist · Companies (platform_admin) · Model pricing (platform_admin)
- Users: ชื่อ, email, role ในบริษัทนี้ (admin/developer/viewer), สังกัดบริษัทอื่น, login ล่าสุด · sync จาก SSO ของระบบ Phalcon
- คำอธิบายสิทธิ์ของแต่ละ role
- Egress allowlist: `host_pattern`, port, `allow_private_ip`, scope (platform/company), ใช้โดย
- Model pricing: provider, model, ราคา input/output ต่อ 1K (ต้องใส่ราคาจริง)

## 12. Settings — Companies (platform_admin)

![Settings — Companies](screens/12-settings-companies.png)

**อ้างอิง PRD:** 7.2, 7.7, 12 (Company deletion)

- ตาราง: code, ชื่อ, `external_ref`, จำนวนสมาชิก, จำนวน agents, cost เดือนนี้เทียบ budget, สถานะ, Suspend/Activate, Destroy DEK
- platform_admin เห็นเฉพาะตัวเลขรวม — ไม่เห็น secret, knowledge หรือเนื้อหาบทสนทนา และสร้าง connection แทนบริษัทไม่ได้
- ขั้นตอนลบบริษัท: suspend (deployment ตอบ 403) → destroy DEK → purge ข้อมูลและไฟล์ MinIO

---

## 13. Tools (Phase 2)

![Tools](screens/13-tools.png)

**อ้างอิง PRD:** 6.5, 7.5

- ตาราง tool: kind (http/builtin/python), access (read/write), `auth_mode` (delegated/service), เปิด/ปิด
- Approval queue: คำขอ write tool พร้อม payload, run, user และเวลาที่เหลือ (5 นาที) → Approve / Reject
- HTTP tool editor: method, URL, description (ให้ model อ่าน), `access_level`, `auth_mode`, Input JSON Schema, Test call ผ่าน SafeHttpClient

## 14. Knowledge Bases (Phase 2)

![Knowledge Bases](screens/14-knowledge.png)

**อ้างอิง PRD:** 6.6

- รายการไฟล์ OKF แยกตามโฟลเดอร์ (`category`) พร้อมสถานะ queued/processing/ready/failed
- Markdown editor: frontmatter + body · Save & re-index เฉพาะไฟล์ที่เปลี่ยน
- ทดสอบค้นหา: hybrid/vector, α, top-k, lang · ผลลัพธ์แสดง heading path, คะแนนรวม, vector และ keyword
- Import `.md`/`.zip`, Export `.zip`

## 15. Skills (Phase 2)

![Skills](screens/15-skills.png)

**อ้างอิง PRD:** 6.6a

- รายการ skill + สถานะ (draft/published/archived) · skill ที่มี scripts เลื่อนไป Phase 3
- Editor `SKILL.md` + ไฟล์ประกอบ, ขนาดเทียบเพดาน 100 KB, Save draft / Publish
- Versions และ agents ที่ผูกกับ skill

## 16. Evaluation (Phase 3)

![Evaluation](screens/16-evaluation.png)

**อ้างอิง PRD:** 6.7, 6.1a (Evaluation)

- ตาราง eval runs: agent version, dataset (รวม `identity-scope` ที่สร้างจาก in/out_of_scope), status, cost
- ตั้งค่า run ใหม่: agent version, dataset, judge model, metrics · ประมาณการค่าใช้จ่ายก่อนเริ่ม, queue low-priority concurrency 4
- Compare สอง run ต่อ metric, Publish gate (เช่น faithfulness ≥ 0.80), รายการที่คะแนนต่ำสุดพร้อมเหตุผลของ judge

## 17. Workflows (Phase 4)

![Workflows](screens/17-workflows.png)

**อ้างอิง PRD:** 2 (Non-goals ข้อ 1), 6.9

- YAML editor (ไม่มี drag-and-drop) พร้อมเลขบรรทัด
- Validation: schema, จำนวน nodes/edges, error ชี้บรรทัด (เช่น tool ที่ไม่ได้ bind กับ version)
- Graph แบบข้อความ, Import YAML, Validate, Publish

---

## สิ่งที่ยังไม่อยู่ใน mockup

- เนื้อหาแท็บ Tools, Knowledge, Skills, Memory, Guardrails, Advanced ของ Agent editor
- Dashboard มุมมองรวมทุกบริษัทของ platform_admin
- Versions (diff / publish / rollback) ของ agent, Datasets, audit log
- หน้าจอ mobile

## ประวัติการแก้ไข

| Rev | รายละเอียด |
|-----|-----------|
| m3 | ปรับตาม PRD r10: เพิ่ม Agents catalog, แท็บ Identity, API Keys; เอา shared connection ออก; เพิ่ม Destroy DEK, `GET /info`, identity version ใน Runs, dataset identity-scope |
| m2 | แก้ Playground ตามรีวิว (สถานะ done/streaming, mask PII ขา input, ป้าย P2, focus ring) และเพิ่มหน้าครบทุกเมนู |
| m1 | 4 หน้าแรก: Dashboard, Agent editor, Playground, Deployment detail (PRD r8) |
