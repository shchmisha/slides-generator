/**
 * The scenario being edited and the text field that receives values from the picker.
 * Shared by the editors through provide/inject, so cards edit the draft directly instead of
 * passing every field up through events.
 */

import { inject, provide, ref, type InjectionKey, type Ref } from "vue";

type ReadRef<T> = Readonly<Ref<T>>;

import type { Json, ValueInfo } from "../api/types";

export interface InsertTarget {
  element: HTMLTextAreaElement | HTMLInputElement;
  write: (value: string) => void;
}

export interface Editing {
  draft: Ref<Json | null>;
  values: ReadRef<ValueInfo[]>;
  target: Ref<InsertTarget | null>;
  /** JSON-tab text not applied to the draft (being typed, or invalid). */
  jsonInvalid: Ref<boolean>;
  /** Applies pending JSON-tab text now; set while the JSON tab is open. */
  flush: Ref<(() => void) | null>;
}

const KEY: InjectionKey<Editing> = Symbol("editing");

export function provideEditing(
  draft: Ref<Json | null>,
  values: ReadRef<ValueInfo[]>,
  jsonInvalid: Ref<boolean> = ref(false),
): Editing {
  const editing: Editing = { draft, values, target: ref(null), jsonInvalid, flush: ref(null) };
  provide(KEY, editing);
  return editing;
}

export function useEditing(): Editing {
  const editing = inject(KEY);
  if (!editing) throw new Error("useEditing() outside a scenario editor");
  return editing;
}
