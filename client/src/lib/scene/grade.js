const LUMA = [0.299, 0.587, 0.114]
const hue = new Float64Array(9)
const tone = new Float64Array(9)

export function gradeMatrix(out, brightness, contrast, saturation, angle) {
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  hue[0] = 0.213 + cos * 0.787 + sin * 0.213
  hue[1] = 0.715 - cos * 0.715 + sin * 0.715
  hue[2] = 0.072 - cos * 0.072 - sin * 0.928
  hue[3] = 0.213 - cos * 0.213 + sin * 0.143
  hue[4] = 0.715 + cos * 0.285 - sin * 0.285
  hue[5] = 0.072 - cos * 0.072 + sin * 0.142
  hue[6] = 0.213 - cos * 0.213 - sin * 0.787
  hue[7] = 0.715 - cos * 0.715 + sin * 0.715
  hue[8] = 0.072 + cos * 0.928 + sin * 0.072
  const gain = brightness * contrast
  const grey = 1 - saturation
  for (let row = 0; row < 3; row++) {
    const sum = hue[row * 3] + hue[row * 3 + 1] + hue[row * 3 + 2]
    for (let column = 0; column < 3; column++) tone[row * 3 + column] = gain * (saturation * hue[row * 3 + column] + grey * LUMA[column] * sum)
  }
  return out.set(tone[0], tone[1], tone[2], tone[3], tone[4], tone[5], tone[6], tone[7], tone[8])
}

export function setGrade(uniforms) {
  const brightness = uniforms.u_brightness.value
  const contrast = uniforms.u_contrast.value
  gradeMatrix(uniforms.u_grade.value, brightness, contrast, uniforms.u_saturation.value, uniforms.u_hue.value)
  uniforms.u_grade_lift.value = brightness * (0.5 - 0.5 * contrast)
}
