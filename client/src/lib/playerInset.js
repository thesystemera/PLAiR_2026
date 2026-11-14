const TAB_BAR_PX = 64
const PANEL_GAP_PX = 16

const followers = new Set()
let height = 80

export const playerInset = {
  height: () => height,
  set(next) {
    if (next === height) return
    height = next
    for (const follow of followers) follow(next)
  },
  follow(apply) {
    followers.add(apply)
    apply(height)
    return () => followers.delete(apply)
  },
}

export const insetBottom = (node, px) => { node.style.bottom = `${px}px` }
export const insetBottomAboveTabs = (node, px) => { node.style.bottom = `${px + TAB_BAR_PX}px` }
export const insetBottomWithGap = (node, px) => { node.style.bottom = `calc(${px}px + 1rem)` }
export const insetPaddingBottom = (node, px) => { node.style.paddingBottom = `${px + PANEL_GAP_PX}px` }
