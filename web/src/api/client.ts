import axios from 'axios'

const BASE = 'http://127.0.0.1:7472'
export const api = axios.create({ baseURL: BASE })

export const ws_url = (path: string) => `ws://127.0.0.1:7472${path}`
