import { Request, Response, NextFunction } from 'express';

export function errorHandler(
  err: Error,
  req: Request,
  res: Response,
  _next: NextFunction
): void {
  console.error(`Unhandled error [${req.method} ${req.path}]:`, err.message, err.stack);
  if (res.headersSent) {
    res.end();
    return;
  }
  res.status(500).json({ error: 'Internal server error' });
}
