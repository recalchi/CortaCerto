import axios from 'axios'

const BASE = 'http://localhost:7472'
export const api = axios.create({ baseURL: BASE })

export const ws_url = (path: string) => `ws://localhost:7472${path}`
