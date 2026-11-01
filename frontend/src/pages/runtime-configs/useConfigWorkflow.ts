import { useEffect, useReducer } from 'react';
import type { Detail, Review } from './contracts';
import { stringifyRuntime } from './json';

export type View = 'list' | 'overview' | 'edit' | 'review' | 'history' | 'create' | 'secrets';
interface State {
  view: View; detail?: Detail; text: string; savedText: string; comment: string;
  source: number | null; review?: Review; reviewedText?: string; latest?: Detail;
}
type Action = { type: 'view'; view: View } | { type: 'open'; detail: Detail; view?: View }
  | { type: 'edit'; text?: string; source?: number }
  | { type: 'text'; value: string } | { type: 'comment'; value: string }
  | { type: 'saved'; detail: Detail } | { type: 'review'; review: Review; text: string }
  | { type: 'latest'; detail: Detail } | { type: 'rebase' } | { type: 'invalidate' } | { type: 'discard' };
const initial: State = { view: 'list', text: '', savedText: '', comment: '', source: null };
function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'view': return { ...state, view: action.view };
    case 'open': {
      const text = stringifyRuntime(action.detail.draftPayload ?? action.detail.publishedPayload ?? {}, 2);
      return { ...initial, view: action.view ?? 'overview', detail: action.detail, text, savedText: text };
    }
    case 'edit': return { ...state, view: 'edit', text: action.text ?? state.text, source: action.source ?? state.source, review: undefined, reviewedText: undefined };
    case 'text': return { ...state, text: action.value, review: undefined, reviewedText: undefined };
    case 'comment': return { ...state, comment: action.value };
    case 'saved': {
      const text = stringifyRuntime(action.detail.draftPayload ?? {}, 2);
      return { ...state, detail: action.detail, text, savedText: text, review: undefined, reviewedText: undefined };
    }
    case 'review': return { ...state, view: 'review', review: action.review, reviewedText: action.text };
    case 'latest': return { ...state, latest: action.detail };
    case 'rebase': return state.latest ? { ...state, detail: state.latest, savedText: stringifyRuntime(state.latest.draftPayload ?? state.latest.publishedPayload ?? {}, 2), latest: undefined, review: undefined, reviewedText: undefined, view: 'edit' } : state;
    case 'invalidate': return { ...state, view: state.view === 'review' ? 'edit' : state.view, review: undefined, reviewedText: undefined };
    case 'discard': {
      const text = state.detail ? stringifyRuntime(state.detail.draftPayload ?? state.detail.publishedPayload ?? {}, 2) : '';
      return { ...state, text, savedText: text, comment: '', source: null, review: undefined, reviewedText: undefined, latest: undefined };
    }
  }
}
export function useConfigWorkflow(externalDirty: boolean) {
  const [state, dispatch] = useReducer(reducer, initial);
  const dirty = (state.view === 'edit' || state.view === 'review') && (state.text !== state.savedText || Boolean(state.comment));
  useEffect(() => {
    if (!dirty && !externalDirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, externalDirty]);
  return { state, dispatch, dirty };
}
