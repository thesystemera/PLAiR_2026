(() => {
  const label = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 && e.textContent.trim() === '3D Lit Artwork')
  if (!label) return 'no toggle (open User > settings)'
  let row = label
  for (let i = 0; i < 6 && row; i++) {
    const button = [...row.querySelectorAll('button')].find(b => /^(ON|OFF)$/.test(b.textContent.trim()))
    if (button) { if (button.textContent.trim() !== globalThis.__litWant) button.click(); return 'lit ' + globalThis.__litWant }
    row = row.parentElement
  }
  return 'button not found'
})()
