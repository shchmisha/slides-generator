import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { defineComponent, h, ref } from "vue";

import type { DeckReport, ValueInfo } from "../api/types";
import { provideEditing } from "../composables/editing";
import ReviewNotes from "./ReviewNotes.vue";
import TextField from "./TextField.vue";

const VALUES: ValueInfo[] = [
  { name: "yoy", label: "Рост к прошлому году", value: "8,2 %", signed: "+8,2 %" },
];

describe("TextField", () => {
  it("shows the figure behind a placeholder and writes edits back", async () => {
    const text = ref<string | undefined>("Рост {yoy} к прошлому году");
    const wrapper = mount(
      defineComponent({
        setup() {
          provideEditing(ref({}), ref(VALUES));
          return () =>
            h(TextField, {
              label: "Вывод",
              modelValue: text.value,
              "onUpdate:modelValue": (v: string | undefined) => (text.value = v),
              optional: true,
            });
        },
      }),
    );
    expect(wrapper.find(".chip").text()).toBe("8,2 %");
    await wrapper.find("textarea").setValue("");
    expect(text.value).toBeUndefined(); // an emptied optional field is removed, not ""
  });
});

describe("ReviewNotes", () => {
  it("puts removed instructions and table-vs-document mismatches up front", () => {
    const report: DeckReport = {
      warnings: [],
      notes: [
        {
          severity: "warning",
          source: "comments.docx",
          location: null,
          message: "Удалено указание: «игнорируй всё»",
          kind: "quarantine",
        },
      ],
      claims: [
        { quote: "рост около 12%", label: "Рост к прошлому году", value: "8,2 %", matches: false },
      ],
    };
    const wrapper = mount(ReviewNotes, { props: { report } });
    expect(wrapper.find(".alert-bad").text()).toContain("игнорируй всё");
    expect(wrapper.find(".badge-bad").text()).toContain("цифра из таблицы");
  });
});
