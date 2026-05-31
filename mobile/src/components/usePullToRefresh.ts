import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";

/**
 * Pull-to-refresh customizado para WEB.
 *
 * O react-native-web NÃO implementa o gesto de arrastar do <RefreshControl>,
 * então no navegador (especialmente no celular) o "puxar para atualizar" não
 * funciona sozinho. Este hook adiciona esse gesto via eventos de toque do DOM.
 *
 * No mobile nativo (APK) o hook é um no-op — as telas já usam <RefreshControl>.
 *
 * Uso:
 *   const scrollTop = useRef(0);
 *   const { wrapperRef, pull } = usePullToRefresh(() => scrollTop.current, fetchDados);
 *   ...
 *   <View ref={wrapperRef} style={{ flex: 1 }}>
 *     <PullIndicator pull={pull} refreshing={loading} />
 *     <FlatList
 *       onScroll={(e) => { scrollTop.current = e.nativeEvent.contentOffset.y; }}
 *       scrollEventThrottle={16}
 *       ...
 *     />
 *   </View>
 */
export function usePullToRefresh(
  getScrollTop: () => number,
  onRefresh: () => void | Promise<void>,
  threshold: number = 60
): { wrapperRef: (node: any) => void; pull: number } {
  // Callback ref: dispara quando o nó monta/desmonta — funciona mesmo em telas
  // que só renderizam a lista depois de um estado de "carregando...".
  const [node, setNode] = useState<any>(null);
  const wrapperRef = useCallback((n: any) => setNode(n ?? null), []);

  const [pull, setPull] = useState(0);
  const pullRef = useRef(0);
  const startY = useRef(0);
  const active = useRef(false);

  // Mantém os callbacks sempre atualizados sem precisar re-anexar os listeners.
  const cb = useRef({ getScrollTop, onRefresh });
  cb.current = { getScrollTop, onRefresh };

  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (!node || typeof node.addEventListener !== "function") return;

    const setP = (v: number) => {
      pullRef.current = v;
      setPull(v);
    };

    // Bloqueia o pull-to-refresh nativo do navegador (recarregaria a página
    // inteira e deslogaria o usuário, já que o token fica em memória).
    const prevOverscroll = document.body.style.overscrollBehaviorY;
    document.body.style.overscrollBehaviorY = "contain";

    const onStart = (e: any) => {
      if (cb.current.getScrollTop() <= 0) {
        startY.current = e.touches[0].clientY;
        active.current = true;
      }
    };
    const onMove = (e: any) => {
      if (!active.current) return;
      const dy = e.touches[0].clientY - startY.current;
      if (dy > 0 && cb.current.getScrollTop() <= 0) {
        if (e.cancelable) e.preventDefault(); // segura a rolagem enquanto puxa
        setP(Math.min(dy * 0.5, threshold + 40)); // resistência ao arrasto
      } else if (dy <= 0) {
        active.current = false;
        setP(0);
      }
    };
    const onEnd = () => {
      if (pullRef.current >= threshold) {
        Promise.resolve(cb.current.onRefresh()).catch(() => {});
      }
      active.current = false;
      setP(0);
    };

    node.addEventListener("touchstart", onStart, { passive: true });
    node.addEventListener("touchmove", onMove, { passive: false });
    node.addEventListener("touchend", onEnd, { passive: true });
    node.addEventListener("touchcancel", onEnd, { passive: true });

    return () => {
      document.body.style.overscrollBehaviorY = prevOverscroll;
      node.removeEventListener("touchstart", onStart);
      node.removeEventListener("touchmove", onMove);
      node.removeEventListener("touchend", onEnd);
      node.removeEventListener("touchcancel", onEnd);
    };
  }, [node, threshold]);

  return { wrapperRef, pull };
}
