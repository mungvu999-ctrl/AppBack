import { z } from 'zod';

import {
  HOUSE_ELEVATION_RANGE_MM,
  HOUSE_HEIGHT_RANGE_MM,
  HOUSE_LEVEL_SOURCES,
  HOUSE_QUESTION_CODES,
  HOUSE_STOREY_LABELS,
  HOUSE_VIEW_KINDS,
} from '@/api/schemas/houseProposal';

/**
 * Thân 200 của `GET /api/projects/{project_id}/house-proposal` (N52, HS-03), ghim chặt cho cổng H1 (L4-N07, W16).
 *
 * Schema của FE (`src/api/schemas/houseProposal.ts`) cố ý KHÔNG `.strict()`, mã/nhãn/nguồn là chuỗi tự do và số
 * ngoài khoảng bị `.catch` bỏ im lặng — đúng cho màn (bảng phụ không được làm hỏng màn), nhưng cổng thì mù: BE đổi
 * tên trường, thêm mã câu hỏi, gửi cốt ngoài khoảng đều "giải đạt". Bản này cho golden của cổng:
 * `.strict()` mọi tầng, enum lấy từ hằng của FE ở SHA ghim (không trôi), cốt/chiều cao trong khoảng FE nhận,
 * `reason`/`textStoreyNo` chỉ ở `ORDER_TEXT_CONFLICT`, `isSection` chỉ là `true` (vắng khi không, W2),
 * công tắc tắt thì `floors`/`runIds` rỗng. Runtime FE không đổi.
 *
 * Cấm nới bằng `.passthrough()` (B0-07 [9]).
 */
const unit = z.number().min(0).max(1);
const intIn = (range: { readonly min: number; readonly max: number }) => z.number().int().min(range.min).max(range.max);

const HouseQuestionStrictSchema = z
  .object({
    code: z.enum(HOUSE_QUESTION_CODES),
    reason: z.enum(['title', 'level']).optional(),
    textStoreyNo: z.number().int().optional(),
  })
  .strict()
  .refine((q) => q.code === 'ORDER_TEXT_CONFLICT' || (q.reason === undefined && q.textStoreyNo === undefined), {
    message: 'reason/textStoreyNo chỉ đi kèm ORDER_TEXT_CONFLICT',
    path: ['reason'],
  });

const HouseFloorProposalStrictSchema = z
  .object({
    floorId: z.string().min(1),
    kind: z.enum(HOUSE_VIEW_KINDS),
    kindConfidence: unit,
    storeyLabel: z.enum(HOUSE_STOREY_LABELS).optional(),
    storeyNo: z.number().int().optional(),
    storeyConfidence: unit,
    elevationMm: intIn(HOUSE_ELEVATION_RANGE_MM).optional(),
    elevationConfidence: unit.optional(),
    elevationSource: z.enum(HOUSE_LEVEL_SOURCES).optional(),
    heightMm: intIn(HOUSE_HEIGHT_RANGE_MM).optional(),
    heightConfidence: unit.optional(),
    heightSource: z.enum(HOUSE_LEVEL_SOURCES).optional(),
    questions: z.array(HouseQuestionStrictSchema),
    isSection: z.literal(true).optional(),
  })
  .strict();

export const HouseProposalStrictSchema = z
  .object({
    enabled: z.boolean(),
    floors: z.array(HouseFloorProposalStrictSchema),
    runIds: z.array(z.string().min(1)),
  })
  .strict()
  .refine((p) => p.enabled || (p.floors.length === 0 && p.runIds.length === 0), {
    message: 'công tắc tắt thì floors và runIds rỗng',
    path: ['floors'],
  });

/**
 * Thân 200 của `GET /api/projects/{project_id}/drawing-sets/{set_id}/building` (NHA-06a).
 *
 * FE chưa có schema cho route này (NHA-06c); bản strict này là hợp đồng của cổng cho tới lúc đó. Tầng dùng lại
 * `HouseFloorProposalStrictSchema` (`floorId` ở đây là mã hình của bộ). Mỗi trường mái là một giá trị kèm độ tin,
 * nguồn và các hình nó được đọc ra; trường tuỳ chọn vắng chứ không `null` (W2). Công tắc tắt hoặc bộ chưa qua phân
 * tích thì không có `proposal`.
 */
const BUILDING_SOURCES = [
  'reviewer',
  'plan+section',
  'roofPlan',
  'section',
  'elevation',
  'plan',
  'convention',
  'default',
] as const;

const MeasuredStrictSchema = z
  .object({
    value: z.union([z.number(), z.string().min(1)]),
    confidence: unit,
    source: z.enum(BUILDING_SOURCES),
    viewIds: z.array(z.string().min(1)),
  })
  .strict();

const RoofSpecStrictSchema = z
  .object({
    levelRef: z.string().min(1),
    form: MeasuredStrictSchema,
    pitchDeg: MeasuredStrictSchema,
    quarterTurns: MeasuredStrictSchema,
    eaveElevationMm: MeasuredStrictSchema.optional(),
    overhangMm: MeasuredStrictSchema,
    ridgeElevationMm: MeasuredStrictSchema.optional(),
    parapetMm: MeasuredStrictSchema.optional(),
    material: MeasuredStrictSchema.optional(),
  })
  .strict();

const BuildingConflictStrictSchema = z
  .object({
    key: z.string().min(1),
    field: z.enum(['elevationMm', 'pitchDeg']),
    candidates: z.array(MeasuredStrictSchema).min(2),
    chosenSource: z.enum(BUILDING_SOURCES).optional(),
  })
  .strict();

const RuleHitStrictSchema = z
  .object({
    key: z.string().min(1).max(256),
    code: z.string().regex(/^[TM]-\d{2}$/),
    level: z.enum(['error', 'warning', 'hint']),
    floorRefs: z.array(z.string().min(1)),
    values: z.array(z.union([z.number(), z.string().min(1)])),
  })
  .strict();

// NHA-06b: giá trị đang hiệu lực của từng tầng, và quyết định đã lưu của người duyệt (đúng dạng thân `building` N59).
const LevelSpecStrictSchema = z
  .object({
    levelRef: z.string().min(1),
    elevationMm: MeasuredStrictSchema.optional(),
    heightMm: MeasuredStrictSchema.optional(),
  })
  .strict();

const LevelDecisionStrictSchema = z
  .object({
    levelRef: z.string().min(1),
    elevationMm: z.number().int().optional(),
    heightMm: z.number().int().optional(),
  })
  .strict();

const RoofDecisionStrictSchema = z
  .object({
    levelRef: z.string().min(1),
    skip: z.boolean(),
    form: z.enum(['flat', 'shed', 'gable', 'hip']).optional(),
    pitchDeg: z.number().optional(),
    quarterTurns: z.number().int().optional(),
    eaveElevationMm: z.number().int().optional(),
    ridgeElevationMm: z.number().int().optional(),
    overhangMm: z.number().int().optional(),
    parapetMm: z.number().int().optional(),
    material: z.enum(['tile', 'metal', 'shingle']).optional(),
  })
  .strict();

const BuildingDecisionsStrictSchema = z
  .object({
    levels: z.array(LevelDecisionStrictSchema),
    roofs: z.array(RoofDecisionStrictSchema),
    picks: z.record(z.string().min(1), z.enum(BUILDING_SOURCES)),
    seen: z.array(z.string().min(1)),
  })
  .strict();

export const SetBuildingStrictSchema = z
  .object({
    enabled: z.boolean(),
    proposal: z
      .object({
        unitSystem: MeasuredStrictSchema,
        floors: z.array(HouseFloorProposalStrictSchema),
        levels: z.array(LevelSpecStrictSchema),
        roofs: z.array(RoofSpecStrictSchema),
        conflicts: z.array(BuildingConflictStrictSchema),
        violations: z.array(RuleHitStrictSchema),
      })
      .strict()
      .optional(),
    decisions: BuildingDecisionsStrictSchema.optional(),
  })
  .strict()
  .refine((b) => b.enabled || (b.proposal === undefined && b.decisions === undefined), {
    message: 'công tắc tắt thì không có proposal và decisions',
    path: ['proposal'],
  });
