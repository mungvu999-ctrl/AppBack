import { ML_MODEL_FAMILIES } from '@/api/schemas/adminMl';

/**
 * "H1 ngữ cảnh" (HOP-DONG-MOI §0.2 B): luật liên tham chiếu mà zod
 * cố ý **không** kiểm, vì một thực thể lệch trong zod làm hỏng cả lượt giải mã
 * ở gateway FE. Chạy **sau** khi mẫu đã giải đạt, trên đầu ra của schema, kèm
 * tham số đường (`pathParams`, snake_case như đường của bảng hợp đồng) và `context`
 * mà test gắn bằng `attach_context` (B0-07 [2]).
 *
 * Mỗi luật trả `null` khi đạt, hoặc một câu nêu mã luật và chỗ lệch.
 */

type Json = Record<string, unknown>;
type Rule = (data: Json, pathParams: Readonly<Record<string, string>>, context: Json) => string | null;

/** Mọi giá trị của khoá `levelId` ở bất kỳ độ sâu nào trong `value`. */
function levelIdsIn(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) {
    value.forEach((item) => levelIdsIn(item, out));
  } else if (value !== null && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      if (key === 'levelId' && typeof child === 'string') {
        out.push(child);
      } else {
        levelIdsIn(child, out);
      }
    }
  }
  return out;
}

/** Các mục của một trang `CursorPageSchema` (N1, N7, N17, N23). */
function itemsOf(data: Json): Json[] {
  return data.items as Json[];
}

/** N16: `level.id === {floor_id}`; mọi `levelId` trong `layer`, `axes`, `dimensions` bằng `level.id`. */
const n16: Rule = (data, pathParams) => {
  const levelId = (data.level as Json).id;
  if (levelId !== pathParams.floor_id) {
    return `N16: level.id "${String(levelId)}" khác floor_id "${String(pathParams.floor_id)}"`;
  }
  const stray = levelIdsIn([data.layer, data.axes, data.dimensions]).find((id) => id !== levelId);
  return stray === undefined ? null : `N16: levelId "${stray}" khác level.id "${String(levelId)}"`;
};

/** N15: `levels` sắp theo `order`; mọi `levelId` của đồ thị trỏ tới một level có thật. */
const n15: Rule = (data) => {
  const { levels, ...rest } = data.graph as Json;
  const list = levels as Json[];
  const unsorted = list.findIndex((level, i) => i > 0 && (level.order as number) < (list[i - 1]!.order as number));
  if (unsorted !== -1) {
    return `N15: levels không sắp theo order (vị trí ${unsorted})`;
  }
  const known = new Set(list.map((level) => level.id));
  const stray = levelIdsIn(rest).find((id) => !known.has(id));
  return stray === undefined ? null : `N15: levelId "${stray}" không trỏ tới level nào`;
};

/** N17: `sequence` giảm dần ngặt. */
const n17: Rule = (data) => {
  const items = itemsOf(data);
  const bad = items.findIndex((item, i) => i > 0 && (item.sequence as number) >= (items[i - 1]!.sequence as number));
  return bad === -1 ? null : `N17: sequence không giảm dần (vị trí ${bad})`;
};

/** N1: `areaM2` làm tròn 2 chữ số thập phân. */
const n1: Rule = (data) => {
  const bad = itemsOf(data).find((item) => {
    const area = item.areaM2 as number;
    return Number(area.toFixed(2)) !== area;
  });
  return bad === undefined ? null : `N1: areaM2 ${String(bad.areaM2)} có hơn 2 chữ số thập phân`;
};

/** N7: mục sắp theo `Floor.order`, test gửi thứ tự tầng qua `context.floorOrder` (danh sách id). */
const n7: Rule = (data, _pathParams, context) => {
  const order = context.floorOrder;
  if (!Array.isArray(order)) {
    return 'N7: thiếu context.floorOrder (test gọi attach_context(response, floorOrder=[...]))';
  }
  const positions = itemsOf(data).map((item) => order.indexOf(item.floorId));
  const missing = positions.indexOf(-1);
  if (missing !== -1) {
    return `N7: floorId của mục ${missing} không có trong context.floorOrder`;
  }
  const bad = positions.findIndex((position, i) => i > 0 && position <= positions[i - 1]!);
  return bad === -1 ? null : `N7: mục không sắp theo Floor.order (vị trí ${bad})`;
};

/** N23: đủ 3 họ của `ML_MODEL_FAMILIES`, mỗi họ đúng một lần. */
const n23: Rule = (data) => {
  const families = itemsOf(data).map((item) => item.family);
  const complete =
    families.length === ML_MODEL_FAMILIES.length && ML_MODEL_FAMILIES.every((family) => families.includes(family));
  return complete ? null : `N23: họ ${JSON.stringify(families)} khác ${JSON.stringify(ML_MODEL_FAMILIES)}`;
};

/** K33: có `endedAt` ⇒ `status === "completed"`; lượt hỏng không bao giờ có `endedAt`. */
const progress: Rule = (data) =>
  data.endedAt !== undefined && data.status !== 'completed'
    ? `K33: có endedAt mà status "${String(data.status)}"`
    : null;

const RULES: Readonly<Record<string, Rule>> = {
  drawings_complete_upload: progress,
  drawings_init_upload: progress,
  drawings_list_latest_uploads: n7,
  drawings_read_progress: progress,
  drawings_upload_chunk: progress,
  ml_list_families: n23,
  projects_list_summaries: n1,
  spatial_read_graph: n15,
  spatial_read_layer: n16,
  streams_open_progress: progress,
  versions_list_versions: n17,
};

/** Luật ngữ cảnh của một thao tác, hay `undefined` khi thao tác không có luật nào. */
export function contextRuleFor(operationId: string): Rule | undefined {
  // `hasOwn`: tên như `constructor` không được rơi vào thuộc tính của Object.prototype.
  return Object.hasOwn(RULES, operationId) ? RULES[operationId] : undefined;
}
