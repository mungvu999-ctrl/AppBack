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
